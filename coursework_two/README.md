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

The backtest reads CW1 PostgreSQL directly.  Bring the CW1 Docker stack
up first (`postgres_db_cw` on port 5439).

```bash
cd coursework_two
poetry install                 # or: pip install -r requirements.txt

# Test suite (87 tests, ≈ 8 s; DB-dependent integration tests
# auto-skip if the CW1 schema is unreachable)
poetry run pytest test/

# Full out-of-sample backtest
poetry run python Main.py --mode full --start 2023-07-01 --end 2026-03-31

# γ × λ sensitivity grid via combinatorial purged cross-validation
poetry run python Main.py --mode sensitivity --start 2023-07-01 --end 2026-03-31

# Eight-variant factor ablation
poetry run python Main.py --mode ablation --start 2023-07-01 --end 2026-03-31

# Crisis-window stress (COVID, 2022 rate shock, Q4 2025 reversal)
poetry run python Main.py --mode stress

# Post-backtest analytics (read the parquet outputs, no DB required)
poetry run python Main.py --mode monte_carlo
poetry run python Main.py --mode regime_perf
```

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
poetry run pytest test/ -v --cov=engine --cov=analytics --cov-report=term-missing
```

87 tests across engine and analytics.  14 PIT integration tests are
DB-dependent and auto-skip without the CW1 schema.

## License

MIT — Team Kolmogorov · UCL MSc Banking and Digital Finance · 2026.

## Key references

Vayanos & Woolley (2013), Fama & French (2015), Carhart (1997), Asness,
Frazzini & Pedersen (2019), Ledoit & Wolf (2004), López de Prado (2016,
2018, 2020), Bailey & López de Prado (2014), Bailey et al. (2017),
Moreira & Muir (2017), Korn et al. (2017), Agrawal & Goyal (2013),
Politis & Romano (1994), Newey & West (1987).  Full bibliography in
[PLAN.md §18](PLAN.md) and the report.
