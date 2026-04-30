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
    the YAML config.  Produces all 17 Parquet artefacts in ``output/``.

``sensitivity``
    Execute the γ × λ grid search with Combinatorial Purged CV.
    Emits ``sensitivity_grid.parquet``.  Runs 15 × 66 = 990 backtest folds
    in parallel via joblib.

``ablation``
    Re-run the backtest across eight factor-weight variants (full
    four-factor, three-factor, two-factor, and four leave-one-out
    versions).  Emits ``ablation_results.parquet``.

``stress``
    Re-run on three crisis windows (COVID 2020, 2022 rate shock, Q4 2025
    reversal) plus Monte Carlo permutation test.

``monte_carlo``
    Circular-block-bootstrap 10⁴ NAV paths from ``portfolio_returns.parquet``.

``regime_perf``
    Per-regime × per-strategy metric decomposition.

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

``portfolio_returns.parquet`` carries the strategy columns plus three
benchmarks and the per-leg / risk-free decomposition:

+-------------------+----------------------------------------------+
| Column            | Meaning                                      |
+===================+==============================================+
| dynamic_gross     | Pre-cost return                              |
+-------------------+----------------------------------------------+
| dynamic_net_20bp  | 20 bp/side cost (headline)                   |
+-------------------+----------------------------------------------+
| dynamic_net_30bp  | 30 bp/side cost (stress)                     |
+-------------------+----------------------------------------------+
| static_net_20bp   | Static 50/50 momentum + value variant        |
+-------------------+----------------------------------------------+
| bandit_net_20bp   | Linear Thompson Sampling variant             |
+-------------------+----------------------------------------------+
| hrp_net_20bp      | Hierarchical Risk Parity construction        |
+-------------------+----------------------------------------------+
| long_leg          | Long-only sub-portfolio return               |
+-------------------+----------------------------------------------+
| short_leg         | Short-only sub-portfolio return              |
+-------------------+----------------------------------------------+
| benchmark_ew      | **Primary** equal-weighted universe          |
+-------------------+----------------------------------------------+
| benchmark_spx     | S&P 500 total-return reference               |
+-------------------+----------------------------------------------+
| benchmark_50_50   | 50 % EW + 50 % SPX blended reference         |
+-------------------+----------------------------------------------+
| rf_rate           | DGS3MO risk-free monthly rate                |
+-------------------+----------------------------------------------+

See ``analytics/performance.py`` for full metric computation.
