<div align="center">

# CW2 Multi-Factor L/S Equity Backtest Engine

### Production-Grade Backtest for Team Kolmogorov's 4-Factor Strategy

*Sector-neutral · dollar-neutral · VIX-regime + dispersion dynamic weighting · Contextual Thompson Sampling · CPCV · Deflated Sharpe*

[Engine](#engine) &middot; [Analytics](#analytics) &middot; [Results](#results) &middot; [Integration with CW1](#cw1cw2-integration)

</div>

---

## Overview

CW2 is a **natural continuation of CW1**.  CW1 built the data pipeline (678
equities × 11 data streams × triple-database).  CW2 uses those tables directly
— no data is duplicated — to backtest the **four-factor sector-neutral
dollar-neutral long/short equity strategy** formalised in CW1 report §3.

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
# Scripts assume CW1 infra (postgres_db_cw) is running
cd coursework_two
poetry install              # or use .venv with pip

# Smoke test
poetry run pytest test/

# Full backtest (full OOS)
poetry run python Main.py --mode full --start 2023-07-01 --end 2026-03-20

# Sensitivity grid with CPCV
poetry run python Main.py --mode sensitivity

# Factor ablation
poetry run python Main.py --mode ablation

# Crisis-window stress
poetry run python Main.py --mode stress
```

## Data Contract (engine → analytics)

Seven Parquet files written to `output/` define the engine↔analytics boundary
(PLAN §6).  Specialists read these only:

| File | Description |
|---|---|
| `portfolio_returns.parquet` | Monthly returns: dynamic gross + net 20/30bp, static, bandit, 3 benchmarks |
| `portfolio_weights.parquet` | Per-stock weights per strategy per date |
| `factor_scores.parquet` | Raw + orthogonalised z-scores + composite per stock |
| `factor_ic.parquet` | Per-factor Spearman + Pearson IC vs next-month returns |
| `factor_premia.parquet` | Fama-MacBeth β per factor per date (§5.9) |
| `regime_log.parquet` | VIX level / regime / dispersions / dynamic weights |
| `exposure_log.parquet` | Gross/net/var99/es99/vol-scalar/DD-scalar/turnover/HHI |
| `bandit_log.parquet` | Thompson Sampling posteriors + arm selected + reward |
| `sensitivity_grid.parquet` | γ × λ × CPCV-fold Sharpe-deflated metrics |
| `ablation_results.parquet` | 5-variant factor ablation outcomes |
| `comparison_results.parquet` | Static vs VIX-only vs dispersion-only vs combined |
| `stress_results.parquet` | COVID / 2022-rate / Q4-2025 stress outcomes |
| `backtest_metadata.parquet` | config_hash · data_sha256 · git_sha · seed |

## Results (Real CW1 Data, 2023-07 → 2026-03, 32 months)

| Variant | Sharpe | Annualised Return | Max DD | Annual Vol |
|---|---|---|---|---|
| **Dynamic Gross** | **1.29** | **+11.9%** | **−6.4%** | **9.1%** |
| Dynamic Net 20bp | 0.76 | +6.7% | −7.6% | 9.1% |
| Dynamic Net 30bp | ... | ... | ... | ... |
| Static Net 20bp | 0.79 | +7.2% | −6.8% | 9.3% |
| Bandit Net 20bp | 0.75 | +6.3% | −6.2% | 8.6% |
| Benchmark EW (Universe) | 0.81 | +10.6% | −8.7% | 13.5% |
| Benchmark ^GSPC (reference) | 1.20 | +14.6% | −7.8% | 12.1% |

**Statistical rigour (PLAN §5.7 + 5.18):**

- Block-bootstrap 95% CI for net Sharpe: [−0.36, +2.02] — upper bound reaches the Sharpe-2 aspiration, but point estimate does not
- Deflated Sharpe threshold (N=15 trials): 1.77 — observed 0.76 < threshold, meaning we cannot reject H₀ of zero true-Sharpe at 95% confidence given 32 months
- Minimum Backtest Length to prove Sharpe ≥ 1 at 95%: ≫ 32 months — the OOS window is statistically under-powered (transparent disclosure)

**Risk profile (institutional appeal):**

- ~9% realised annualised vol vs 13.5% for EW benchmark → 33% lower risk
- 7.6% max drawdown vs 8.7% for EW → superior tail protection
- |β| ≈ 0 → genuinely market-neutral diversifier
- Calmar ratio 0.88 (dynamic net) vs 1.22 for benchmark — lower but with meaningful DD advantage

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
# 59 tests, all green
```

## License

MIT — Team Kolmogorov · IFTE0003 · UCL MSc Banking and Digital Finance · March 2026

## References

See [PLAN.md §18](PLAN.md) for full bibliography (70+ citations).  Key anchors:
Vayanos-Woolley (2013), Asness-Frazzini-Pedersen (2019), Ledoit-Wolf (2004),
López de Prado (2016, 2018, 2020), Bailey-López de Prado (2014), Moreira-Muir
(2017), Agrawal-Goyal (2013), Fama-MacBeth (1973), Fama-French (2015).
