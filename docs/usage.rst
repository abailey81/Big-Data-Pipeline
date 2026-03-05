Usage Guide
===========

The pipeline is invoked via the ``Main.py`` entry point using Poetry:

.. code-block:: bash

   poetry run python Main.py --env_type <dev|docker> [OPTIONS]

Command-Line Arguments
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 25 10 15 50

   * - Argument
     - Required
     - Default
     - Description
   * - ``--env_type``
     - Yes
     - --
     - Environment: ``dev`` (localhost) or ``docker``
   * - ``--frequency``
     - No
     - ``None`` (6-year backfill)
     - Run cadence: ``daily`` (5d), ``weekly`` (14d), ``monthly`` (35d), ``quarterly`` (95d). Omit for full 6-year backfill.
   * - ``--sources``
     - No
     - all
     - Data sources: ``prices``, ``fundamentals``, ``fx``, ``vix``, ``risk_free_rate``, ``benchmark``, ``ratios``, ``esg``, ``sentiment``
   * - ``--date_run``
     - No
     - today
     - Run date in ``YYYY-MM-DD`` format
   * - ``--start_date``
     - No
     - derived
     - Override start date for data download
   * - ``--end_date``
     - No
     - date_run
     - Override end date for data download
   * - ``--tickers``
     - No
     - all 678
     - Specific ticker symbols to process
   * - ``--init_schema``
     - No
     - false
     - Initialise database schema before run
   * - ``--dry_run``
     - No
     - false
     - Validate configuration without downloading
   * - ``--schedule``
     - No
     - false
     - Run on recurring schedule via APScheduler

Frequency-Based Lookback
--------------------------

When no explicit ``--start_date`` is given, the lookback window is determined
by the ``--frequency`` flag:

.. list-table::
   :header-rows: 1

   * - Frequency
     - Lookback
     - Use Case
   * - ``daily``
     - 5 days
     - Incremental daily updates (covers business week)
   * - ``weekly``
     - 14 days
     - Weekly refresh with buffer
   * - ``monthly``
     - 35 days
     - Month-end processing
   * - ``quarterly``
     - 95 days
     - Quarterly rebalance window

If ``--frequency`` is omitted and ``--start_date`` is not provided, the pipeline
defaults to a **full 6-year backfill** (configured via ``lookback_years: 6`` in
``config/conf.yaml``). Use ``--start_date`` to set a custom start date.

**Full 6-year backfill (default — recommended for initial setup):**

.. code-block:: bash

   .venv/bin/python Main.py --env_type dev

For incremental runs after backfill, use ``--frequency daily``.

Common Usage Examples
-----------------------

**Full daily run (all data sources, all tickers):**

.. code-block:: bash

   poetry run python Main.py --env_type dev --frequency daily

**Monthly fundamentals-only run:**

.. code-block:: bash

   poetry run python Main.py --env_type dev --frequency monthly --sources fundamentals

**Custom date range for specific tickers:**

.. code-block:: bash

   poetry run python Main.py --env_type dev --start_date 2020-01-01 --end_date 2025-12-31 --tickers AAPL MSFT VOD.L

**Docker environment (inside container network):**

.. code-block:: bash

   poetry run python Main.py --env_type docker --frequency daily

**Dry run to validate configuration:**

.. code-block:: bash

   poetry run python Main.py --env_type dev --dry_run

**Scheduled recurring run (APScheduler):**

.. code-block:: bash

   poetry run python Main.py --env_type dev --frequency daily --schedule

This starts a background scheduler that re-runs the pipeline at the
configured frequency. Press ``Ctrl+C`` to stop.

Running Tests
--------------

.. code-block:: bash

   # Unit tests only (no external dependencies)
   poetry run pytest -m "not integration"

   # Full suite (requires PostgreSQL + MinIO)
   poetry run pytest

   # With coverage report
   poetry run pytest --cov=modules --cov-report=html

Code Quality
-------------

.. code-block:: bash

   # Linting
   poetry run flake8 modules/ test/

   # Formatting
   poetry run black modules/ test/ Main.py
   poetry run isort modules/ test/ Main.py

   # Security scanning
   poetry run bandit -r modules/ -c pyproject.toml
