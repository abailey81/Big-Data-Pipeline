Installation
============

CW2 is a natural continuation of CW1 — it reads directly from the CW1
PostgreSQL schema ``systematic_equity`` on port 5439.  No data is duplicated.

Prerequisites
-------------

* Python 3.10+
* Docker (for CW1 infrastructure containers)
* CW1 pipeline already run → ``systematic_equity.*`` tables populated
* Poetry (optional but preferred)

Setup
-----

.. code-block:: bash

    # 1. Ensure CW1 infrastructure is up
    cd /path/to/team_01
    docker compose up -d --build   # postgres on 5439

    # 2. Install CW2 dependencies
    cd coursework_two
    poetry install                  # or: pip install -r <requirements>

    # 3. Verify DB connectivity
    poetry run python -c "from engine.data_loader import DataLoader; \
                          from engine.config import load_config; \
                          print('OK' if DataLoader(load_config()).health_check() else 'DB unreachable')"

    # 4. Run a smoke test
    poetry run python -m pytest test/ -x

    # 5. Run the full backtest
    poetry run python Main.py --mode full --start 2023-07-01 --end 2026-03-31
