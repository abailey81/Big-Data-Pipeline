# `analysis/` — L/S statistical-inference scripts

Ad-hoc analysis scripts that layer statistical inference, factor
attribution, and cost-stress diagnostics on top of the
`coursework_two/` backtest outputs.  Merged via PR #7.

## Contents

| Script | Purpose | Side effects |
|---|---|---|
| `run_inference_ls.py` | Bootstrap CI, Deflated Sharpe, PSR, Minimum Backtest Length across the four L/S variants (Dynamic, Static, Bandit, HRP) | Read-only on `coursework_two/output/`; writes `analysis/output/ls_inference.csv` |
| `run_attribution_ls.py` | FF5 + Momentum regression with Newey-West HAC (lag 4, Andrews 1991) using the shared `analytics/fama_french.py` helper | Read-only on `coursework_two/output/`; writes `analysis/output/ls_ff5_mom_attribution.csv` |
| `run_cost_stress_ls_v2.py` | Four cost levels (10 / 20 / 50 / 100 bp per side) via two Main.py reruns, remapping `*_net_20bp` / `*_net_30bp` columns to the logical cost per run | Temporarily rewrites `coursework_two/config/backtest_config.yaml` (with `try/finally` restore); writes per-stress backtest parquets to `analysis/_cost_stress_output/` by default — does **not** overwrite `coursework_two/output/`; writes `analysis/output/ls_cost_stress.csv` |

## How to run (from the repo root)

```bash
python3 analysis/run_inference_ls.py
python3 analysis/run_attribution_ls.py
python3 analysis/run_cost_stress_ls_v2.py                   # scratch output
python3 analysis/run_cost_stress_ls_v2.py --output-dir /tmp/my_scratch
```

Scripts honour Poetry if it's on PATH, and fall back to `python3` otherwise.

## Reproducibility caveat — committed CSVs are data-snapshot-bound

The CSVs in `analysis/output/` are the result of running against a specific
CW1 Postgres snapshot.  The SHA-256 of that snapshot is visible in
`coursework_two/output/backtest_metadata.parquet::data_snapshot_sha256`
(field `data_snapshot_sha256`).

If a teammate re-runs the scripts against a **different** CW1 snapshot
(e.g. after a re-seed, a new daily ingestion, or a different branch of
the CW1 data pipeline) the numbers in their regenerated CSVs will
differ.  This is expected: the scripts read the engine's parquet output,
not the raw CW1 tables, so they inherit whatever snapshot produced those
parquets.

Anyone citing a specific number in the report should pair it with the
`data_snapshot_sha256` value from the run that produced it, mirroring the
reproducibility convention in `coursework_two/CHANGELOG.md §0.3.2`.
