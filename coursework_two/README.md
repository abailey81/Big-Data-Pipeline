<div align="center">

# CW2 Multi-Factor L/S Equity Backtest Engine

### Production-Grade Backtest for Team Kolmogorov's L/S Equity Strategy

*Sector-neutral · dollar-neutral · VIX-regime + dispersion dynamic weighting · Contextual Thompson Sampling · CPCV · Deflated Sharpe · Iterative weight cap · Empirical portfolio β*

[Engine](#engine) &middot; [Analytics](#analytics) &middot; [Results](#results) &middot; [Integration with CW1](#cw1cw2-integration) &middot; [Audit trail](#audit-remediation-v030)

</div>

---

## Status — v0.3.0 (2026-04-22)

- **Final strategy:** 2-factor composite (momentum 0.50 + value 0.50) after
  post-fix IC diagnostic found quality IC to be nearly-significantly
  *negative* (t = −1.95, p = 0.06) and sentiment data to be structurally
  insufficient (< 10 articles/month for ≈ 90 % of backtest window).
- **All audit findings** either verified-and-fixed (weight cap, β, trade
  ledger, HRP, long/short legs, CPCV, Monte Carlo, regime performance)
  or rejected-with-reasoning (PIT filter, permutation scope, DB coupling,
  bandit claim, Sharpe convention).
- **Documentation trail:** see [AUDIT_FINDINGS_MATRIX.md](AUDIT_FINDINGS_MATRIX.md)
  for every audit claim × brief × current-state,
  [FACTOR_REVIEW_2026-04-22.md](FACTOR_REVIEW_2026-04-22.md) for the factor
  decision with empirical backing, and
  [CHANGELOG.md](CHANGELOG.md) §0.3.0 for the complete change log.

---

## Overview

CW2 is a **natural continuation of CW1**.  CW1 built the data pipeline (678
equities × 11 data streams × triple-database).  CW2 uses those tables directly
— no data is duplicated — to backtest the sector-neutral dollar-neutral
long/short equity strategy.  The factor set was reduced from the original
CW1 4-factor composite to a 2-factor composite after a post-fix IC diagnostic
(quality fix exposed a nearly-significant negative IC in the 2023–2026 "junk
rally" regime; sentiment cannot be historically reconstructed from the
available data).  All four factors remain computed so that the diagnostic
IC table in the report carries the complete picture.

The engine implements every component specified in the CW2 Task Allocation
Guide plus the tiered sophistication layer in [PLAN.md](PLAN.md):

- **Dependency-injected event-driven backtest** (10 swappable components, PLAN §7.1)
- **Strict point-in-time discipline** (CW1 §5.1 trap: ``report_date`` not ``period_end``)
- **Denoised Ledoit-Wolf covariance** (López de Prado 2020) with turnover penalty
- **Hierarchical Risk Parity** as robustness comparison
- **Contextual Thompson Sampling** for adaptive weight selection — the classical-RL analogue of §5.4
- **Three-stage risk scaler**: 99% HVaR → conditional vol targeting → drawdown-control overlay
- **Block bootstrap + Deflated Sharpe + PSR + Minimum Backtest Length** statistical inference
- **Fama-MacBeth cross-sectional attribution** + FF5+Momentum Newey-West regression
- **Kyle's-λ capacity estimator** for fund-pitch credibility
- **CPCV** (López de Prado 2018) for data-snooping-robust hyperparameter tuning
- **Multi-benchmark suite**: equal-weight universe (canonical) + S&P 500 + 50/50 Cash/Market blend

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  CW1 PostgreSQL (port 5439, schema = systematic_equity)          │
│  daily_prices · fundamentals · fx_rates · vix_data · rfr         │
│  company_static · news_sentiment · company_ratios · esg_scores   │
└──────────────────────────┬──────────────────────────────────────┘
                           │  read-only PIT SQL
                ┌──────────▼──────────┐
                │   engine/             │   DEVELOPERS
                │   (14 modules)        │
                └──────────┬──────────┘
                           │  7 Parquet data-contract files
                ┌──────────▼──────────┐
                │   analytics/          │   SPECIALISTS
                │   (8 modules)         │
                └──────────┬──────────┘
                           │  14+ charts + metric tables
                ┌──────────▼──────────┐
                │   Report (docs/)      │   IPOs
                └─────────────────────┘
```

## CW1↔CW2 Integration

Tight coupling — CW2 is *not* a standalone pipeline.  It reads:

| Source table | CW1 column read | CW2 module |
|---|---|---|
| `daily_prices` | `adj_close_price`, `currency`, `volume` | `engine/data_loader.py` |
| `fundamentals` | EAV pivot `field_name × field_value` on `report_date` | same |
| `company_ratios` | `book_to_price_hist`, `earnings_to_price_hist`, ... | same |
| `company_static` | `gics_sector`, `country`, `symbol` (TRIM'd) | same |
| `fx_rates` | `close_rate` for GBP/EUR/CAD/CHF ↔ USD conversion | same |
| `vix_data` | `close_price` for VIX regime classification | same |
| `risk_free_rate` | `rate_pct` (DGS3MO) for Sharpe denominators | same |
| `benchmark_index` | `adj_close_price` for S&P 500 reference | `engine/benchmark.py` |
| `news_sentiment` | `sentiment_score` (VADER + financial boost, PLAN §5.4 / CW1 §3.2 Eq. 7) | same |

CW2 uses CW1's currency-inference helpers (`.L → GBP`, `.PA → EUR`, `.S → CHF`)
verbatim from CW1's ``modules/processing/ticker_utils.py``.

**ESG considered and rejected** — CW1 §2.4 replaced ESG with sentiment due to
34.5% coverage.  Re-introducing ESG on a backtest would either (a) exclude 65%
of the universe, or (b) introduce look-ahead bias (single-snapshot at
2026-03-20).  Both violate PLAN §7.3.  CW2 follows CW1's decision. An opt-in
``--esg-screen`` flag is available for comparison runs.

## Quick Start

```bash
# Scripts assume CW1 infra (postgres_db_cw) is running on port 5439
cd coursework_two
poetry install              # or use .venv with pip

# Smoke test — 76 tests, ~7s
poetry run pytest test/

# Full backtest (full OOS 2023-07 → 2026-03, ~3 min)
poetry run python Main.py --mode full --start 2023-07-01 --end 2026-03-31

# CPCV γ × λ sensitivity — 15 × 66 = 990 rows, ~45 min
poetry run python Main.py --mode sensitivity --start 2023-07-01 --end 2026-03-31

# Factor ablation — 8 variants (full_4factor, no_momentum, no_value,
# no_quality, no_sentiment, no_sentiment_3factor, mom_val_only, mom_val_qual)
poetry run python Main.py --mode ablation --start 2023-07-01 --end 2026-03-31

# Crisis-window stress (COVID, 2022 rate shock, Q4 2025 reversal)
poetry run python Main.py --mode stress

# Post-backtest analytics (read output/*.parquet, no DB dependency)
poetry run python Main.py --mode monte_carlo   # 10,000 bootstrap NAV paths
poetry run python Main.py --mode regime_perf   # per-regime × per-strategy metrics
```

## Data Contract (engine → analytics)

Seventeen Parquet files written to `output/` define the engine↔analytics
boundary (PLAN §6, extended in v0.3.0).  Specialists read these only:

| File | Rows (final run) | Description |
|---|---|---|
| `portfolio_returns.parquet` | 32 | Monthly returns: dynamic gross + net 20/30bp, static, bandit, **HRP**, 3 benchmarks, **long_leg / short_leg**, rf_rate |
| `portfolio_weights.parquet` | 5,370 | Per-stock weights per strategy per date — 5 % cap enforced |
| `factor_scores.parquet` | 16,874 | Raw + orthogonalised z-scores + composite per stock |
| `factor_ic.parquet` | 128 | Per-factor Spearman + Pearson IC vs next-month returns |
| `factor_premia.parquet` | 128 | Fama-MacBeth β per factor per date (§5.9) |
| `regime_log.parquet` | 33 | VIX level / regime / dispersions / dynamic weights |
| `exposure_log.parquet` | 33 | Gross/net/**empirical β**/var99/es99/vol-scalar/DD-scalar/turnover/HHI |
| `bandit_log.parquet` | 33 | Thompson Sampling posteriors + arm selected + reward |
| `sensitivity_grid.parquet` | **990** | γ × λ × **66 CPCV folds** (15 grid points × 66 fold combinations, with deflated Sharpe per grid point) |
| `ablation_results.parquet` | **8** | full_4factor + 4 single-factor drops + no_sentiment_3factor + mom_val_only + mom_val_qual |
| `stress_results.parquet` | 4 | COVID 2020 / 2022 rate shock / Q4 2025 / full OOS |
| `permutation_test.parquet` | 1 | Monte Carlo dynamic-vs-static Sharpe-gap p-value (10k permutations) |
| `permutation_null_distribution.parquet` | 10,000 | Null Sharpe-gap distribution under dynamic/static label-shuffle |
| **`trade_ledger.parquet`** | **2,171** | §7.9 immutable per-trade audit log — 13 fields incl. UUID rebalance_id, seed, data_snapshot_sha256 |
| **`monte_carlo_paths.parquet`** | **320,000** | §7.5 — 10,000 circular-block-bootstrap NAV paths × 32 months |
| **`regime_performance.parquet`** | 12 | §7.6 — per-regime × per-strategy metric decomposition |
| `backtest_metadata.parquet` | 1 | config_hash · data_sha256 · git_sha · seed |

## Results (Real CW1 Data, 2023-07 → 2026-03, 32 months — v0.3.0)

### Headline — 2-factor momentum + value composite

| Variant | Sharpe | Ann. Return | Max DD | Ann. Vol |
|---|---|---|---|---|
| **Static Net 20bp** | **+1.418** | **+16.6%** | **−8.0%** | 11.7% |
| **Dynamic Net 20bp** | **+1.316** | **+15.7%** | **−8.8%** | 12.5% |
| HRP Net 20bp | +1.592 | +7.0% | **−2.7%** | 4.4% |
| Bandit Net 20bp | +0.778 | +9.3% | −8.2% | 12.1% |
| Benchmark EW (Universe) | +0.915 | +11.6% | −8.7% | 13.5% |
| Benchmark ^GSPC (reference) | +1.206 | +14.7% | −7.8% | 12.1% |

### Ablation — 8 variants (see [analytics/ablation.py](analytics/ablation.py))

| Variant | Weights (mom/val/qual/sent) | Sharpe | Δ from full_4factor |
|---|---|---|---|
| **mom_val_only** (adopted) | 0.50 / 0.50 / 0.00 / 0.00 | **+1.418** | **+0.456** |
| no_quality | 0.40 / 0.40 / 0.00 / 0.20 | +1.418 | +0.456 |
| no_sentiment / no_sentiment_3factor | 0.35 / 0.35 / 0.30 / 0.00 | +0.983 | +0.021 |
| full_4factor (CW1 default) | 0.30 / 0.30 / 0.25 / 0.15 | +0.962 | 0 |
| mom_val_qual | 0.40 / 0.40 / 0.20 / 0.00 | +0.901 | −0.061 |
| no_value | 0.44 / 0.00 / 0.35 / 0.21 | +0.480 | −0.482 |
| no_momentum | 0.00 / 0.44 / 0.35 / 0.21 | −0.136 | −1.098 |

### Factor IC diagnostic (final run — all 4 factors computed for report)

| Factor | mean Spearman IC | t-stat | p-value | Decision |
|---|---|---|---|---|
| Momentum | **+0.0645** | **+2.44** | **0.020** | keep (significant) |
| Value | +0.0158 | +1.00 | 0.325 | keep (diversifying, ρ=0.04 w/ momentum) |
| Quality | **−0.0175** | **−1.95** | **0.061** | **drop** — nearly-significant negative after construction fix |
| Sentiment | 0.0000 | n/a | n/a | **drop** — structural data gap, no usable history |

### CPCV sensitivity (2-factor — 990 rows, [sensitivity_grid.parquet](output/sensitivity_grid.parquet))

| | λ = 0.05 | λ = 0.10 | λ = 0.15 |
|---|---|---|---|
| γ = 0.00 | **1.883 / 0.561** | 1.824 / 0.529 | 1.681 / 0.451 |
| γ = 0.25 | 1.722 / 0.473 | 1.782 / 0.506 | 1.690 / 0.456 |
| γ = 0.50 | 1.689 / 0.455 | 1.763 / 0.496 | 1.635 / 0.425 |
| γ = 0.75 | 1.689 / 0.455 | 1.758 / 0.493 | 1.635 / 0.425 |
| γ = 1.00 | 1.694 / 0.458 | 1.762 / 0.495 | 1.641 / 0.428 |

*Format: `mean CPCV Sharpe / deflated Sharpe (15 trials, Bailey-López de Prado 2014)`.  Best point (γ = 0.00, λ = 0.05) implies the dispersion-based dynamic overlay does not add value at the 2-factor level in this sample — reported honestly per PLAN §14 P5.*

### Statistical rigour (PLAN §5.7 + 5.18)

- Block-bootstrap 95 % CI for Dynamic Net 20bp Sharpe: full returns
  series (32 monthly observations) produces a bootstrap CI published in
  the notebook via `circular_block_bootstrap_sharpe`.
- Deflated Sharpe threshold (n_trials = 15): 1.77.
- Minimum Backtest Length to prove Sharpe ≥ 1 at 95 %: exceeds the
  32-month OOS window — transparent disclosure in the report.
- Monte Carlo (10,000 circular-block-bootstrap paths, 6-month blocks) in
  `output/monte_carlo_paths.parquet` for the §6 fund-pitch envelope.

### Risk profile (institutional appeal)

- 12.5 % realised annualised vol (dynamic 20bp) vs 13.5 % EW benchmark
- 8.8 % max drawdown vs 8.7 % benchmark; HRP variant achieves 2.7 %
- Empirical portfolio β range [−0.13, +0.26], mean +0.08 — near-neutral
- Calmar 1.79 (dynamic net) vs 1.34 (benchmark) — a structural improvement

## Design Sophistication Delivered

| PLAN Tier-2 item | Delivered | File |
|---|---|---|
| Denoised Ledoit-Wolf | ✅ MP eigenvalue clipping | [engine/portfolio.py:59](engine/portfolio.py#L59) |
| Turnover-penalised MinVar | ✅ L2 penalty, λ=50 | [engine/portfolio.py:181](engine/portfolio.py#L181) |
| HRP comparison | ✅ López de Prado 2016 | [engine/portfolio.py:215](engine/portfolio.py#L215) |
| Contextual Thompson Sampling | ✅ 12 arms × 12-dim context | [engine/bandit.py](engine/bandit.py) |
| Combinatorial Purged CV | ✅ 12 groups × 2 test × 2mo purge | [analytics/sensitivity.py](analytics/sensitivity.py) |
| Block bootstrap + Deflated SR + PSR | ✅ Politis-Romano 1994 | [analytics/performance.py:138](analytics/performance.py#L138) |
| FF5+Mom regression (Newey-West HAC) | ✅ statsmodels HAC | [analytics/attribution_analysis.py](analytics/attribution_analysis.py) |
| Fama-MacBeth | ✅ per-date cross-section | [engine/attribution.py](engine/attribution.py) |
| Factor orthogonalisation (Gram-Schmidt) | ✅ sector-neutral sequential | [engine/factors.py:143](engine/factors.py#L143) |
| Liquidity filter | ✅ min ADV + bottom %ile | [engine/data_loader.py:300](engine/data_loader.py#L300) |
| Conditional volatility targeting | ✅ Moreira-Muir 2017 | [engine/risk_scaler.py:78](engine/risk_scaler.py#L78) |
| Drawdown-control overlay | ✅ Korn et al. 2017 | [engine/risk_scaler.py:106](engine/risk_scaler.py#L106) |
| Minimum Backtest Length | ✅ Bailey-Borwein-LdP-Zhu 2017 | [analytics/performance.py:200](analytics/performance.py#L200) |
| Capacity via Kyle's λ | ✅ Amihud illiquidity | [engine/attribution.py:92](engine/attribution.py#L92) |
| MC permutation test | ✅ dynamic-vs-static p-value | [analytics/stress.py:72](analytics/stress.py#L72) |
| Advanced multi-benchmark | ✅ EW-universe + S&P + 50/50 | [engine/benchmark.py](engine/benchmark.py) |

## Tests

```bash
poetry run pytest test/ -v --cov=engine --cov=analytics --cov-report=term-missing
# 76 tests, all green (17 DB-dependent PIT integration tests auto-skip without CW1 infra)
```

## Audit Remediation v0.3.0

The v0.3.0 release (2026-04-22) addresses every audit finding from the
team review after cross-referencing each claim against PLAN.md.  Full
trace in [AUDIT_FINDINGS_MATRIX.md](AUDIT_FINDINGS_MATRIX.md).

**Fixes (verified on a fresh 32-month re-run):**

| # | Fix | File(s) | Before → After |
|---|---|---|---|
| P0 | Iterative weight cap | [engine/portfolio.py](engine/portfolio.py) | max 14.1 % / 14.1 % / 17.3 % → **5.00 % exact** across 3 strategies × 33 periods |
| P0 | Empirical portfolio β | [engine/backtest.py](engine/backtest.py) | identically 0.0 → empirical CAPM β range [−0.13, +0.26], mean +0.08 |
| P0 | Trade ledger populated | [engine/backtest.py](engine/backtest.py) `_emit_trade_ledger` | 0 rows → **2,171 rows** with 13 PLAN §7.9 fields |
| P0 | HRP variant wired | [engine/portfolio.py](engine/portfolio.py) `construction_override` | all `None` → 32/32 populated, Sharpe 1.59 |
| P0 | CPCV 66-fold run | [analytics/sensitivity.py](analytics/sensitivity.py) | 15 single-fold rows → **990 rows** (15 γ × λ × 66 CPCV folds) |
| P0 | Quality construction | [engine/factors.py](engine/factors.py) `compute_quality` | 1-snapshot fallbacks (economically backwards) → 400+-snapshot `_hist` variants (`roe_hist`, `debt_to_equity_hist`, `profit_margin_hist`) |
| P1 | `long_leg` / `short_leg` | [engine/backtest.py](engine/backtest.py) | hardcoded 0.0 → realised per-leg monthly returns from `_simulate_monthly_return` |
| P1 | `monte_carlo_paths.parquet` | [analytics/monte_carlo.py](analytics/monte_carlo.py) (new) | missing → 320,000 rows (10k paths × 32 months) |
| P1 | `regime_performance.parquet` | [analytics/regime_performance.py](analytics/regime_performance.py) (new) | missing → 12 rows (3 regimes × 4 strategies) |

**Audit claims rejected with reasoning:** PIT filter on `report_date`
(PLAN §7.3 mandates this), permutation test scope (PLAN §5.13 defines
dynamic-vs-static null), DB-coupled reproducibility (PLAN §16.1 requires
direct SQL), bandit "never explored" (factually wrong — all 12 arms
selected), Sharpe convention (PLAN §8.1 reports raw + deflated + PSR +
bootstrap CI — no change needed).

## License

MIT — Team Kolmogorov · IFTE0003 · UCL MSc Banking and Digital Finance · March 2026

## References

See [PLAN.md §18](PLAN.md) for full bibliography (70+ citations).  Key anchors:
Vayanos-Woolley (2013), Asness-Frazzini-Pedersen (2019), Ledoit-Wolf (2004),
López de Prado (2016, 2018, 2020), Bailey-López de Prado (2014), Moreira-Muir
(2017), Agrawal-Goyal (2013), Fama-MacBeth (1973), Fama-French (2015).
