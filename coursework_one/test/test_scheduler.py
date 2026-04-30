"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Unit tests for APScheduler pipeline scheduler
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

from unittest.mock import MagicMock, patch

import pytest

from modules.utils.scheduler import APSCHEDULER_AVAILABLE, FREQUENCY_CRON, PipelineScheduler


class TestFrequencyCron:
    """Tests for cron frequency definitions."""

    def test_daily_cron(self):
        cron = FREQUENCY_CRON["daily"]
        assert cron["hour"] == 18
        assert cron["day_of_week"] == "mon-fri"

    def test_weekly_cron(self):
        cron = FREQUENCY_CRON["weekly"]
        assert cron["day_of_week"] == "fri"

    def test_monthly_cron(self):
        cron = FREQUENCY_CRON["monthly"]
        assert cron["day"] == 1

    def test_quarterly_cron(self):
        cron = FREQUENCY_CRON["quarterly"]
        assert cron["month"] == "1,4,7,10"

    def test_all_frequencies_present(self):
        assert "daily" in FREQUENCY_CRON
        assert "weekly" in FREQUENCY_CRON
        assert "monthly" in FREQUENCY_CRON
        assert "quarterly" in FREQUENCY_CRON


class TestPipelineScheduler:
    """Tests for PipelineScheduler."""

    def test_init_defaults(self):
        scheduler = PipelineScheduler()
        assert scheduler.frequency == "daily"
        assert scheduler.timezone == "UTC"
        assert scheduler._scheduler is None

    def test_init_custom_frequency(self):
        scheduler = PipelineScheduler(frequency="weekly", timezone="Europe/London")
        assert scheduler.frequency == "weekly"
        assert scheduler.timezone == "Europe/London"

    def test_is_available_property(self):
        scheduler = PipelineScheduler()
        assert isinstance(scheduler.is_available, bool)

    @patch("modules.utils.scheduler.APSCHEDULER_AVAILABLE", False)
    def test_schedule_returns_false_when_unavailable(self):
        scheduler = PipelineScheduler()
        result = scheduler.schedule(lambda: None)
        assert result is False

    @patch("modules.utils.scheduler.APSCHEDULER_AVAILABLE", True)
    def test_schedule_invalid_frequency(self):
        scheduler = PipelineScheduler(frequency="hourly")
        result = scheduler.schedule(lambda: None)
        assert result is False

    @patch("modules.utils.scheduler.APSCHEDULER_AVAILABLE", True)
    @patch("modules.utils.scheduler.BackgroundScheduler")
    @patch("modules.utils.scheduler.CronTrigger")
    def test_schedule_success(self, mock_trigger, mock_scheduler_cls):
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        scheduler = PipelineScheduler(frequency="daily")
        result = scheduler.schedule(lambda: None, job_id="test_job")
        assert result is True
        mock_scheduler.add_job.assert_called_once()

    @patch("modules.utils.scheduler.APSCHEDULER_AVAILABLE", True)
    @patch("modules.utils.scheduler.BackgroundScheduler")
    @patch("modules.utils.scheduler.CronTrigger")
    def test_schedule_failure(self, mock_trigger, mock_scheduler_cls):
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        mock_scheduler.add_job.side_effect = Exception("Scheduling error")

        scheduler = PipelineScheduler(frequency="daily")
        result = scheduler.schedule(lambda: None)
        assert result is False

    def test_start_when_no_scheduler(self):
        scheduler = PipelineScheduler()
        scheduler.start()

    def test_stop_when_no_scheduler(self):
        scheduler = PipelineScheduler()
        scheduler.stop()

    def test_stop_with_scheduler(self):
        scheduler = PipelineScheduler()
        mock_sched = MagicMock()
        scheduler._scheduler = mock_sched
        scheduler.stop()
        mock_sched.shutdown.assert_called_once_with(wait=True)
        assert scheduler._scheduler is None

    def test_get_next_run_when_no_scheduler(self):
        scheduler = PipelineScheduler()
        assert scheduler.get_next_run() is None

    def test_get_next_run_no_job(self):
        scheduler = PipelineScheduler()
        mock_sched = MagicMock()
        mock_sched.get_job.return_value = None
        scheduler._scheduler = mock_sched
        assert scheduler.get_next_run() is None
