"""
Tests for configuration, argument parsing, and utility modules.
"""

import os
from datetime import datetime
from unittest.mock import patch

import pytest


class TestArgParser:

    def test_required_env_type(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev"])
        assert args.env_type == "dev"

    def test_docker_env_type(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "docker"])
        assert args.env_type == "docker"

    def test_default_frequency(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev"])
        assert args.frequency is None  # default is full 6-year backfill

    def test_monthly_frequency(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--frequency", "monthly"])
        assert args.frequency == "monthly"

    def test_default_sources(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev"])
        assert args.sources == [
            "prices",
            "fundamentals",
            "fx",
            "vix",
            "risk_free_rate",
            "benchmark",
            "ratios",
            "esg",
            "sentiment",
        ]

    def test_specific_sources(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--sources", "prices", "vix"])
        assert args.sources == ["prices", "vix"]

    def test_date_override(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(
            ["--env_type", "dev", "--start_date", "2023-01-01", "--end_date", "2025-12-31"]
        )
        assert args.start_date == "2023-01-01"
        assert args.end_date == "2025-12-31"

    def test_tickers_override(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--tickers", "AAPL", "MSFT"])
        assert args.tickers == ["AAPL", "MSFT"]

    def test_init_schema_flag(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--init_schema"])
        assert args.init_schema is True

    def test_dry_run_flag(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--dry_run"])
        assert args.dry_run is True

    def test_schedule_flag(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--schedule"])
        assert args.schedule is True

    def test_schedule_default_false(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev"])
        assert args.schedule is False


class TestPostgresConfig:

    def test_direct_values(self, postgres_config_dict):
        from modules.db_ops.postgres_config import PostgresConfig

        config = PostgresConfig(**postgres_config_dict)
        assert config.username == "postgres"
        assert config.port == "5438"
        assert config.database == "fift"

    def test_env_fallback(self):
        from modules.db_ops.postgres_config import PostgresConfig

        with patch.dict(
            os.environ,
            {
                "POSTGRES_USERNAME": "test_user",
                "POSTGRES_PASSWORD": "test_pass",
                "POSTGRES_HOST_DEV": "testhost",
                "POSTGRES_PORT_DEV": "5432",
                "POSTGRES_DATABASE": "testdb",
            },
        ):
            config = PostgresConfig()
            assert config.username == "test_user"
            assert config.host == "testhost"
            assert config.database == "testdb"

    def test_direct_overrides_env(self, postgres_config_dict):
        from modules.db_ops.postgres_config import PostgresConfig

        with patch.dict(os.environ, {"POSTGRES_USERNAME": "env_user"}):
            config = PostgresConfig(**postgres_config_dict)
            assert config.username == "postgres"  # Direct takes precedence


class TestGenerateRunId:

    def test_generates_uuid_string(self):
        from modules.utils import generate_run_id

        run_id = generate_run_id()
        assert isinstance(run_id, str)
        assert len(run_id) == 36  # UUID format
        assert "-" in run_id

    def test_unique_ids(self):
        from modules.utils import generate_run_id

        ids = {generate_run_id() for _ in range(100)}
        assert len(ids) == 100


class TestPipelineLogger:

    def test_logger_exists(self):
        from modules.utils import pipeline_logger

        assert pipeline_logger is not None

    def test_logger_has_info(self):
        from modules.utils import pipeline_logger

        # Should not raise
        pipeline_logger.info("Test log message from test suite")
