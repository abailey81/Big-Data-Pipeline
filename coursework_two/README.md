# CW2 Backtest Engine — Multi-Factor Long/Short Equity

Team Kolmogorov · IFTE0003 Big Data in Quantitative Finance · UCL MSc Banking and Digital Finance

## Overview

A monthly-rebalanced sector-neutral, dollar-neutral long/short equity
strategy on the 678-stock CW1 universe (US, UK, Europe, Canada,
Switzerland).  The implemented composite combines two factors —
momentum (12-1) and value (B/P + E/P + CF/P) — at equal 50/50
weights, after Coursework 1's four-factor proposal was reduced based
on out-of-sample information-coefficient evidence (quality:
IC = −0.018, t = −1.95; sentiment: IC = 0.000 due to a single-snapshot
news table).  Methodology, results, and limitations are documented in
the accompanying report.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  CW1 PostgreSQL (port 5439, schema = systematic_equity)         │
│  daily_prices · fundamentals · fx_rates · vix_data ·            │
│  risk_free_rate · benchmark_index · news_sentiment ·            │
│  company_static · company_ratios                                │
└──────────────────────────┬──────────────────────────────────────┘
                           │  read-only PIT SQL
                ┌──────────▼──────────┐
                │   engine/           │  data loader · factors ·
                │   (15 modules)      │  z-scoring · portfolio ·
                │                     │  bandit · risk scaler ·
                │                     │  costs · backtest loop
                └──────────┬──────────┘
                           │  17 Parquet artefacts
                ┌──────────▼──────────┐
                │   analytics/        │  performance · charts ·
                │   (10 modules)      │  sensitivity · ablation ·
                │                     │  stress · attribution
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │   notebooks/        │  CW2_Tearsheet.ipynb
                │   reports/          │  CW1↔CW2 integration check
                │   docs/             │  Sphinx documentation
                └─────────────────────┘
```

## Quick Start

CW2 reads the CW1 PostgreSQL schema directly — there is no separate
CW2 data layer.  Pick the path that matches your starting state:

| Starting state | Go to |
|---|---|
| Fresh checkout, nothing installed, no DB running | [Path A — Full setup](#path-a--full-setup-from-scratch) |
| CW1 Docker is already running with the schema populated | [Path B — Run CW2 only](#path-b--run-cw2-only-cw1-db-already-up) |
| Just want to refresh the tearsheet from existing parquets | [Path C — Tearsheet only](#path-c--tearsheet-only-no-db-required) |

### Prerequisites

- Python 3.10 – 3.13 (tested on 3.13.x)
- Poetry ≥ 1.5  (`pip install poetry` if missing)
- Docker (or a local Postgres listening on 5439) — only required for Paths A and B
- ≈ 1 GB free disk for `output/`, `docs/_build/`, and the FF-factor cache

### Path A — full setup from scratch

```bash
# 1. CW1 Postgres up (from repo root)
docker compose up -d --build                  # postgres_db_cw on localhost:5439

# 2. Install CW2
cd coursework_two
poetry install                                # ≈ 60 s

# 3. Register a Jupyter kernel that points at Poetry's Python.
#    Required so nbconvert / `jupyter notebook` execute the tearsheet
#    against the venv (Python 3.13 + scipy 1.17) rather than any
#    pre-existing system kernel.
poetry run python -m ipykernel install --user --name=cw2-poetry --display-name="CW2 (poetry venv)"

# 4. Verify DB is reachable
poetry run python -c "from engine.data_loader import DataLoader; \
    from engine.config import load_config; \
    print('DB reachable' if DataLoader(load_config()).health_check() else 'DB unreachable')"

# 5. Continue with Path B
```

### Path B — run CW2 only (CW1 DB already up)

End-to-end pipeline.  Wall-clock ≈ 40 min total; ablation is the long pole.

> **Important:** stay inside `coursework_two/` for every `poetry run` invocation.
> The repository root and `coursework_one/` each have their own `pyproject.toml`
> with separate Poetry venvs.  `cd`-ing into either of those folders and running
> `poetry run` will pick up the wrong project — analysis scripts will fail with
> `ModuleNotFoundError: No module named 'numpy'`.  All CW2 analysis scripts live
> under `coursework_two/analysis/` and resolve their own paths absolutely.

```bash
cd coursework_two

# Engine — writes 17 parquets to coursework_two/output/
poetry run python Main.py --mode full        --start 2023-07-01 --end 2026-03-31   # ~  4 min
poetry run python Main.py --mode sensitivity --start 2023-07-01 --end 2026-03-31   # ~  8 min
poetry run python Main.py --mode ablation    --start 2023-07-01 --end 2026-03-31   # ~ 30 min
poetry run python Main.py --mode stress                                            # ~  1 min
poetry run python Main.py --mode monte_carlo                                       # ~ 30 s
poetry run python Main.py --mode regime_perf                                       # ~  5 s

# Analysis CSVs — read parquets only, no DB.  Stay in coursework_two/.
poetry run python analysis/run_attribution_ls.py     # ~ 10 s — Table 10
poetry run python analysis/run_inference_ls.py       # ~ 30 s — Tables 11–13
poetry run python analysis/run_cost_stress_ls_v2.py  # ~  6 min — Table 14

# Tearsheet — paths are relative to coursework_two/
poetry run python -m jupyter nbconvert --to notebook --execute \
    notebooks/CW2_Tearsheet.ipynb --inplace \
    --ExecutePreprocessor.kernel_name=cw2-poetry \
    --ExecutePreprocessor.timeout=900
poetry run python -m jupyter nbconvert --to html notebooks/CW2_Tearsheet.ipynb
```

**Headline-only fast path** (~ 8 min — skips sensitivity, ablation, cost stress, since those are committed in the repo):

```bash
cd coursework_two && \
poetry run python Main.py --mode full --start 2023-07-01 --end 2026-03-31 && \
poetry run python Main.py --mode stress && \
poetry run python Main.py --mode monte_carlo && \
poetry run python Main.py --mode regime_perf && \
poetry run python analysis/run_attribution_ls.py && \
poetry run python analysis/run_inference_ls.py && \
poetry run python -m jupyter nbconvert --to notebook --execute \
    notebooks/CW2_Tearsheet.ipynb --inplace \
    --ExecutePreprocessor.kernel_name=cw2-poetry \
    --ExecutePreprocessor.timeout=900
```

### Path C — tearsheet only (no DB required)

The 17 parquets in `output/` and the FF-factor cache in
`output/.ff_cache/` are committed.  This re-renders the tearsheet
without touching Postgres:

```bash
cd coursework_two
poetry install
poetry run python -m ipykernel install --user --name=cw2-poetry --display-name="CW2 (poetry venv)"
poetry run python -m jupyter nbconvert --to notebook --execute \
    notebooks/CW2_Tearsheet.ipynb --inplace \
    --ExecutePreprocessor.kernel_name=cw2-poetry \
    --ExecutePreprocessor.timeout=900
poetry run python -m jupyter nbconvert --to html notebooks/CW2_Tearsheet.ipynb
```

The notebook ships with `kernelspec.name = "cw2-poetry"` so Jupyter Lab /
Notebook / VS Code will auto-select the right interpreter once the
kernel is registered.

### What each engine mode does

| Mode | Purpose | Time | Outputs |
|---|---|---:|---|
| `full` | 33 monthly rebalances (Jul 2023 – Feb 2026); Dynamic / Static / Bandit / HRP | 4 min | `portfolio_returns`, `portfolio_weights`, `factor_*`, `regime_log`, `exposure_log`, `bandit_log`, `trade_ledger`, `backtest_metadata` (14 parquets) |
| `sensitivity` | γ × λ grid (15 cells × 66 CPCV folds) with deflated Sharpe per cell | 8 min | `sensitivity_grid.parquet` |
| `ablation` | 8 factor-weight variants — full backtest each | 30 min | `ablation_results.parquet` |
| `stress` | 3 crisis windows + Dynamic-vs-Static permutation test (10⁴ permutations) | 1 min | `stress_results.parquet`, `permutation_test.parquet`, `permutation_null_distribution.parquet` |
| `monte_carlo` | 10⁴ circular-block-bootstrap NAV paths from `portfolio_returns.parquet` | 30 s | `monte_carlo_paths.parquet` |
| `regime_perf` | Per-regime × per-strategy metric decomposition | 5 s | `regime_performance.parquet` |

### Verify outputs

```bash
ls coursework_two/output/*.parquet | wc -l        # expect 17
ls analysis/output/*.csv                          # expect 3 CSVs

poetry run python -c "
import pandas as pd
df = pd.read_parquet('coursework_two/output/portfolio_returns.parquet')
print('Rows (months):', len(df))
print('Date range   :', df.date.min(), '->', df.date.max())
print(df[['static_net_20bp','dynamic_net_20bp','hrp_net_20bp','bandit_net_20bp']].describe().round(4))
"
```

The annualised-return / vol / Sharpe values should match the
[Headline Results](#headline-results) table to two decimal places (small
differences arise if the CW1 snapshot has been refreshed since the
report was frozen — see the `data_snapshot_sha256` reproducibility
caveat below).

### Expected warnings (benign)

The CW1 `news_sentiment` table is a single 2026-03-20 snapshot, so for
every pre-2026-03-20 rebalance the sentiment column is constant and
Spearman / Pearson correlations are mathematically undefined.  You will
see:

- `ConstantInputWarning: An input array is constant; the correlation coefficient is not defined.`
- `RuntimeWarning: invalid value encountered in divide`
- `FutureWarning: The default fill_method='pad' in DataFrame.pct_change is deprecated`

These do not affect any reported number.  The `ZScoreEngine.composite`
sentinel-coverage safeguard redistributes sentiment's zero composite
weight to momentum and value, and `factor_ic.parquet` records
`sentiment_ic = 0.0000` for every rebalance — this is the report's
§2.2.1 finding, not a runtime error.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `CW1 DB unreachable: psycopg2.OperationalError ... port 5439 ... Connection refused` | CW1 Docker is down.  From the repo root: `docker compose up -d --build`. |
| `pyarrow ... Failed ... ModuleNotFoundError: No module named 'pkg_resources'` (on Python 3.13) | Old `poetry.lock` pinning pyarrow 15.  `poetry lock && poetry install` — the committed `pyproject.toml` requires pyarrow ≥ 18 which ships Py 3.13 wheels. |
| `scipy TypeError ... _fitpack_impl.py` during pytest | `poetry run pytest` resolved a system-Python pytest from `PATH` instead of the venv's pytest (a known macOS issue when both Python 3.9 framework and Homebrew Python 3.13 are installed).  Use `poetry run python -m pytest test/` — invoking pytest as a module forces the venv's Python. |
| `scipy TypeError ... _fitpack_impl.py` inside a notebook cell | The Jupyter kernel attached to the notebook is the system `python3` (Python 3.9), not Poetry's Python 3.13.  Register a venv-pointing kernel: `poetry run python -m ipykernel install --user --name=cw2-poetry --display-name="CW2 (poetry venv)"` and either select that kernel in Jupyter or pass `--ExecutePreprocessor.kernel_name=cw2-poetry` to nbconvert. |
| `Cannot install scipy` / `Cannot install pandas-market-calendars` from `poetry install` | Poetry is targeting an externally-managed Python (Homebrew's `/usr/local/lib/python3.13/site-packages`) with a stale `dist-info` that blocks reinstall.  Force an in-project venv: `poetry config virtualenvs.in-project true && poetry env remove --all && poetry install`. |
| Headline numbers differ slightly from the report | The CW1 snapshot has moved since the report was frozen.  The report's tables reference the snapshot whose hash is stamped in `output/backtest_metadata.parquet::data_snapshot_sha256`; current parquets reflect today's snapshot.  This is expected and the analysis CSVs always reflect the current run. |

## Data Contract

The engine writes 17 Parquet files to `output/`.  Downstream analytics
modules read these only — they do not re-invoke the engine.

| File | Description |
|---|---|
| `portfolio_returns.parquet` | Monthly returns: dynamic gross, dynamic net 20/30 bp, static, bandit, HRP, three benchmarks, long_leg, short_leg, rf_rate |
| `portfolio_weights.parquet` | Per-stock weights per strategy per rebalance |
| `factor_scores.parquet`     | Raw and orthogonalised z-scores plus the composite |
| `factor_ic.parquet`         | Per-factor Spearman and Pearson IC versus next-month return |
| `factor_premia.parquet`     | Fama-MacBeth β per factor per date |
| `regime_log.parquet`        | VIX level, regime label, factor dispersions, dynamic weights |
| `exposure_log.parquet`      | Gross/net exposure, empirical β, HVaR/ES, vol- and DD-scalars, turnover, HHI |
| `bandit_log.parquet`        | Thompson-sampling posteriors, arm selected, realised reward |
| `sensitivity_grid.parquet`  | 15 (γ, λ) × 66 CPCV folds with deflated Sharpe |
| `ablation_results.parquet`  | 8 factor-weight variants |
| `stress_results.parquet`    | Per-crisis-window metrics |
| `permutation_test.parquet`  | Dynamic-vs-Static Sharpe gap p-value (10⁴ permutations) |
| `permutation_null_distribution.parquet` | Null distribution from the same permutation test |
| `trade_ledger.parquet`      | Immutable per-trade audit log (13 fields) |
| `monte_carlo_paths.parquet` | 10⁴ circular-block-bootstrap NAV paths |
| `regime_performance.parquet` | Per-regime × per-strategy metrics |
| `backtest_metadata.parquet` | `config_hash`, `data_snapshot_sha256`, `git_sha`, `seed` |

## Headline Results

Out-of-sample window: July 2023 → February 2026 (32 monthly observations),
net of 20 basis points per side.  Numbers are reproducible from the
committed `output/*.parquet` files; methodology and statistical inference
are in the report.

| Variant | Annualised return | Volatility | Raw Sharpe | Excess Sharpe | Max drawdown |
|---|---:|---:|---:|---:|---:|
| Static  Net 20 bp | +17.83 % | 11.39 % | +1.505 | +1.087 | −7.86 % |
| Dynamic Net 20 bp | +16.92 % | 11.67 % | +1.404 | +0.997 | −8.64 % |
| HRP     Net 20 bp |  +7.02 % |  4.33 % | +1.592 | +0.493 | −2.67 % |
| Bandit  Net 20 bp |  +9.69 % | 12.14 % | +0.824 | +0.432 | −10.17 % |
| Equal-weight benchmark (universe) | +11.56 % | 13.52 % | +0.915 | +0.500 | −8.66 % |
| S&P 500 total return | +14.74 % | 12.07 % | +1.206 | +0.829 | −7.81 % |

The Fama-French five-factor + Carhart momentum regression produces
annualised α of +23.97 % (t = 2.353, p = 0.019) on Dynamic and
+25.33 % (t = 2.563, p = 0.010) on Static, both significant at the 5 %
level.  See report §4.2 (Table 10) for the full attribution.

## Reproducibility

Every run stamps `backtest_metadata.parquet` with:

- `config_hash` — SHA-256 prefix of the validated config object
- `data_snapshot_sha256` — fingerprint of the CW1 PostgreSQL payload
- `git_sha` — repository HEAD at run time
- `seed` — fixed at 42 for numpy, random, and the Thompson sampler

The Kenneth-French five-factor + momentum data are cached under
`output/.ff_cache/` after first download; subsequent runs are offline-
deterministic from the cache.  Repeating the FF5 + Mom regression on
the canonical snapshot reproduces the report's Table 10 numbers exactly.

## CW1 Integration

CW2 reads the CW1 schema in place — no data duplication.  Field
mappings:

| CW1 table | CW1 columns used | CW2 module |
|---|---|---|
| `daily_prices` | `adj_close_price`, `currency`, `volume` | `engine/data_loader.py` |
| `fundamentals` | EAV pivot on `report_date` | `engine/data_loader.py` |
| `company_ratios` | `roe_hist`, `book_to_price_hist`, ... (`_hist` variants for PIT-safe time-series) | `engine/data_loader.py` |
| `company_static` | `gics_sector`, `country`, `symbol` | `engine/data_loader.py` |
| `fx_rates` | `close_rate` for GBP/EUR/CAD/CHF→USD | `engine/data_loader.py` |
| `vix_data` | `close_price` for VIX percentile regime | `engine/data_loader.py` |
| `risk_free_rate` | `rate_pct` (DGS3MO) | `engine/data_loader.py` |
| `benchmark_index` | `adj_close_price` (^GSPC) | `engine/benchmark.py` |
| `news_sentiment` | `sentiment_score` (zero-weight in the implemented composite; retained for the IC diagnostic) | `engine/data_loader.py` |

A point-in-time validation report is in
[reports/cw1_integration.md](reports/cw1_integration.md).

## Tests

```bash
poetry run python -m pytest test/ -v --cov=engine --cov=analytics --cov-report=term-missing
```

87 tests across engine and analytics.  14 PIT integration tests are
DB-dependent and auto-skip without the CW1 schema.

## Documentation

API documentation is generated with Sphinx (autodoc + napoleon + viewcode).
A pre-built HTML site is committed under [docs/_build/html/](docs/_build/html/).
Open [docs/_build/html/index.html](docs/_build/html/index.html) in a
browser to navigate the engine and analytics module reference.

To rebuild:

```bash
poetry run python -m sphinx -b html docs docs/_build/html
```

Source RST files live in [docs/](docs/) (`index.rst`, `architecture.rst`,
`installation.rst`, `usage.rst`, `api_engine.rst`, `api_analytics.rst`).

## Tearsheet notebook

The investment tearsheet is at
[notebooks/CW2_Tearsheet.ipynb](notebooks/CW2_Tearsheet.ipynb).  Markdown
narrative is aligned to the submitted report (every numeric claim
references a report Table or Figure); code-cell outputs are stripped at
commit time so the notebook re-renders against the current parquets.
The notebook reads only the `output/*.parquet` artefacts and the cached
Kenneth-French factor data — no PostgreSQL connection is required.  See
[Path C](#path-c--tearsheet-only-no-db-required) for the render command.

## License

MIT — Team Kolmogorov · UCL MSc Banking and Digital Finance · 2026.

## Key references

Vayanos & Woolley (2013), Fama & French (2015), Carhart (1997), Asness,
Frazzini & Pedersen (2019), Ledoit & Wolf (2004), López de Prado (2016,
2018, 2020), Bailey & López de Prado (2014), Bailey et al. (2017),
Moreira & Muir (2017), Korn et al. (2017), Agrawal & Goyal (2013),
Politis & Romano (1994), Newey & West (1987).  Full bibliography in
[PLAN.md §18](PLAN.md) and the report.
