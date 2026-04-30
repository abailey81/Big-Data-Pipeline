"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : APScheduler-based pipeline scheduling
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Provides scheduling capabilities for the data pipeline using
APScheduler, as recommended by the assignment spec:

  "Use scheduling libraries like APScheduler or Airflow for
   more complex scheduling needs."

Supports cron-based and interval-based scheduling for repeated
pipeline execution at configurable frequencies (daily, weekly,
monthly, quarterly).

References:
  - APScheduler documentation: https://apscheduler.readthedocs.io/

"""

from typing import Callable, Optional

from modules.utils.info_logger import pipeline_logger

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    APSCHEDULER_AVAILABLE = True
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None
    IntervalTrigger = None
    APSCHEDULER_AVAILABLE = False

# Cron expressions for each supported frequency
FREQUENCY_CRON = {
    "daily": {"hour": 18, "minute": 0, "day_of_week": "mon-fri"},
    "weekly": {"hour": 18, "minute": 0, "day_of_week": "fri"},
    "monthly": {"hour": 18, "minute": 0, "day": 1},
    "quarterly": {"hour": 18, "minute": 0, "day": 1, "month": "1,4,7,10"},
}


class PipelineScheduler:
    """Scheduler for automated pipeline execution.

    Wraps APScheduler's ``BackgroundScheduler`` to provide
    frequency-based pipeline scheduling with cron triggers.

    :param frequency: Run frequency ('daily', 'weekly', 'monthly', 'quarterly')
    :type frequency: str
    :param timezone: Timezone for cron triggers
    :type timezone: str
    """

    def __init__(self, frequency: str = "daily", timezone: str = "UTC"):
        self.frequency = frequency
        self.timezone = timezone
        self._scheduler: Optional[BackgroundScheduler] = None

    @property
    def is_available(self) -> bool:
        """Check if APScheduler is installed.

        :return: True if APScheduler is available
        :rtype: bool
        """
        return APSCHEDULER_AVAILABLE

    def schedule(self, func: Callable, job_id: str = "cw1_pipeline", **func_kwargs) -> bool:
        """Schedule a function for recurring execution.

        :param func: Function to schedule (typically ``main``)
        :type func: Callable
        :param job_id: Unique job identifier
        :type job_id: str
        :param func_kwargs: Keyword arguments to pass to func
        :return: True if scheduling succeeded
        :rtype: bool
        """
        if not APSCHEDULER_AVAILABLE:
            pipeline_logger.warning(
                "APScheduler not installed — scheduling unavailable. " "Install with: poetry add apscheduler"
            )
            return False

        cron_params = FREQUENCY_CRON.get(self.frequency)
        if cron_params is None:
            pipeline_logger.warning(
                f"Unknown frequency '{self.frequency}' — " f"valid options: {list(FREQUENCY_CRON.keys())}"
            )
            return False

        try:
            self._scheduler = BackgroundScheduler(timezone=self.timezone)
            trigger = CronTrigger(**cron_params, timezone=self.timezone)
            self._scheduler.add_job(
                func,
                trigger=trigger,
                id=job_id,
                kwargs=func_kwargs,
                replace_existing=True,
            )
            pipeline_logger.info(f"Pipeline scheduled: {self.frequency} " f"(cron: {cron_params})")
            return True
        except Exception as e:
            pipeline_logger.error(f"Failed to schedule pipeline: {e}")
            return False

    def start(self):
        """Start the scheduler.

        The scheduler runs in the background; use ``stop()`` or
        a signal handler to shut it down gracefully.
        """
        if self._scheduler is not None:
            self._scheduler.start()
            pipeline_logger.info("Pipeline scheduler started")

    def stop(self):
        """Stop the scheduler gracefully."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=True)
            pipeline_logger.info("Pipeline scheduler stopped")
            self._scheduler = None

    def get_next_run(self, job_id: str = "cw1_pipeline") -> Optional[str]:
        """Get the next scheduled run time.

        :param job_id: Job identifier
        :type job_id: str
        :return: Next run time as ISO string or None
        :rtype: str or None
        """
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None
