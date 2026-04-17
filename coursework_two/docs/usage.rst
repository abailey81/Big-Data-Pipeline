Usage
=====

CLI
---

.. code-block:: bash

    poetry run python Main.py --mode <mode> [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Modes
-----

``full`` (default)
    Run a single backtest from ``dates.oos_start`` to ``dates.oos_end`` in
    the YAML config.  Produces all 9 Parquet artefacts in ``output/``.

``sensitivity``
    Execute the γ × λ grid search with Combinatorial Purged CV (§5.5).
    Emits ``sensitivity_grid.parquet``.  Runs ~15 × 66 = 990 backtest folds
    in parallel via joblib.

``ablation``
    Re-run the backtest five times with each factor removed (weight=0,
    others renormalised).  Emits ``ablation_results.parquet``.

``stress``
    Re-run on three crisis windows (COVID 2020, 2022 rate shock, Q4 2025
    reversal) plus Monte Carlo permutation test (§5.13).

Config
------

All knobs live in ``config/backtest_config.yaml``.  See §5 of PLAN.md for
the full parameter map.  Key knobs:

- ``portfolio.construction``: ``minvar_denoised_lw`` | ``minvar_turnover`` | ``hrp``
- ``portfolio.turnover_penalty_lambda``: L2 penalty on weight changes
- ``dynamic_weights.gamma``: dispersion sensitivity (0 → no tilt)
- ``risk_scaler.vol_target_annual``: target annualised vol (Moreira-Muir 2017)
- ``risk_scaler.dd_control_enabled``: toggle DD overlay (Korn et al. 2017)
- ``bandit.enabled``: run Thompson Sampling variant alongside grid-dynamic

Interpreting output
-------------------

``portfolio_returns.parquet`` has three strategy columns plus three
benchmark columns:

+-----------------+------------------------------+
| Column          | Meaning                      |
+=================+==============================+
| dynamic_gross   | Pre-cost return (Eq 9 comp.) |
+-----------------+------------------------------+
| dynamic_net_20bp| 20 bp/side cost (headline)  |
+-----------------+------------------------------+
| static_net_20bp | Static 30/30/25/15 variant   |
+-----------------+------------------------------+
| bandit_net_20bp | Thompson Sampling variant    |
+-----------------+------------------------------+
| benchmark_ew    | **Primary** EW universe      |
+-----------------+------------------------------+
| benchmark_spx   | S&P 500 market reference     |
+-----------------+------------------------------+

See ``analytics/performance.py`` for full metric computation.
