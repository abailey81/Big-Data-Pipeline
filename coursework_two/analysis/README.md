# `analysis/` — L/S inference, attribution, and cost-stress diagnostics

Three scripts that consume `coursework_two/output/portfolio_returns.parquet`
and produce the supporting numbers cited in the report's §4.2 (FF5+Mom
attribution), §4.3 (statistical inference), and §4.4 (cost stress).

## Contents

| Script | Purpose |
|---|---|
| `run_inference_ls.py`      | Bootstrap CI, deflated Sharpe, probabilistic Sharpe, minimum backtest length across the four L/S variants. |
| `run_attribution_ls.py`    | FF5 + Momentum regression with Newey-West HAC at lag 4 (Andrews 1991) for Dynamic and Static. |
| `run_cost_stress_ls_v2.py` | Four cost levels (10 / 20 / 50 / 100 bp per side) reconstructed from two backtest reruns. |

## Run

```bash
python3 analysis/run_inference_ls.py
python3 analysis/run_attribution_ls.py
python3 analysis/run_cost_stress_ls_v2.py                  # scratch output
python3 analysis/run_cost_stress_ls_v2.py --output-dir /tmp/scratch
```

The cost-stress script writes per-stress backtest parquets to a scratch
directory (default `analysis/_cost_stress_output/`); the canonical
`coursework_two/output/` parquets are not overwritten.  All three scripts
honour Poetry if it is on PATH and fall back to `python3` otherwise.

## Output

CSVs are written to `analysis/output/`:

- `ls_inference.csv` — one row per variant with annualised return / vol /
  Sharpe / drawdown statistics, bootstrap 95 % CI on excess Sharpe, PSR
  at three benchmarks, deflated Sharpe at 15 trials, and minimum-backtest-
  length figures.
- `ls_ff5_mom_attribution.csv` — one row per (variant × factor) coefficient
  with NW-HAC standard errors and significance.  Annualised alpha is
  reported on the geometric `(1+α_m)^12 − 1` convention to match the
  report's headline figure.
- `ls_cost_stress.csv` — eight rows (Dynamic / Static × four cost levels)
  with raw and excess Sharpe and bootstrap CI.

## Snapshot dependence

Numbers are reproducible against a specific CW1 Postgres snapshot whose
SHA-256 is stamped in `coursework_two/output/backtest_metadata.parquet::data_snapshot_sha256`.
A re-run on a different snapshot will produce different numbers; pair
any cited figure with the snapshot hash from the run that produced it,
following the convention in `coursework_two/CHANGELOG.md`.
