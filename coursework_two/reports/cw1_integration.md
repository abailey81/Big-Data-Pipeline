# CW1 ↔ CW2 Integration Validation Report
*Originally generated 2026-04-17; refreshed header 2026-04-22 for v0.3.0.*
**DB:** `postgres@localhost:5439/fift` — schema `systematic_equity`
**CW2 config hash (v0.3.0):** `04a95c0dae3c8a37`
**Git (v0.3.0 HEAD):** see `git rev-parse HEAD`; PLAN config + engine code both pinned.
**CW1 data snapshot SHA-256 (v0.3.0):** `1e9adb7304eff2b1a8acb3e84c7a6302ea41de0dec9fe85126d20ee56e7116d7`
*(Previous run: config `aae3fc77929786c0`, git `cb823727…`, snapshot `4c93491f…`.)*

## 1 · Schema contract
Every table CW2 reads must carry the CW2-expected columns. If CW1 ever drops or renames a column, this check fails loudly.

| Table | Expected columns | Missing? |
|---|---|---|
| `company_static` | country, gics_sector, security, symbol | ✅ |
| `daily_prices` | adj_close_price, cob_date, currency, symbol, volume | ✅ |
| `fundamentals` | field_name, field_value, period_type, report_date, symbol | ✅ |
| `company_ratios` | field_name, field_value, snapshot_date, symbol | ✅ |
| `fx_rates` | close_rate, cob_date, currency_pair | ✅ |
| `vix_data` | close_price, cob_date | ✅ |
| `risk_free_rate` | cob_date, rate_pct | ✅ |
| `benchmark_index` | adj_close_price, cob_date, symbol | ✅ |
| `news_sentiment` | cob_date, sentiment_score, symbol | ✅ |

## 2 · Table row counts and freshness

| Table | Row count | Latest date | Distinct symbols |
|---|---:|---|---:|
| `company_static` | 678 | None | 678 |
| `daily_prices` | 948,403 | 2026-04-15 | 604 |
| `fundamentals` | 195,910 | 2026-03-20 | 604 |
| `company_ratios` | 250,788 | 2026-03-20 | 604 |
| `fx_rates` | 6,254 | 2026-04-14 | 4 |
| `vix_data` | 1,506 | 2026-03-19 | 1 |
| `risk_free_rate` | 1,563 | 2026-03-18 | 1 |
| `benchmark_index` | 7,526 | 2026-03-19 | 5 |
| `news_sentiment` | 625 | 2026-03-20 | 625 |

## 3 · Currency-inference parity (CW1 `ticker_utils.infer_currency`)

| Symbol | Expected | Inferred | OK |
|---|---|---|---|
| `BARC.L` | GBP | GBP | ✅ |
| `BNP.PA` | EUR | EUR | ✅ |
| `SAP.DE` | EUR | EUR | ✅ |
| `ADS.DE` | EUR | EUR | ✅ |
| `SAN.MC` | EUR | EUR | ✅ |
| `RBC.TO` | CAD | CAD | ✅ |
| `NOVN.S` | CHF | CHF | ✅ |
| `NESN.SW` | CHF | CHF | ✅ |
| `AAPL` | USD | USD | ✅ |

## 4 · Factor-coverage breakdown

| Source | Symbols covered | % of universe |
|---|---:|---:|
| prices (daily_prices) | 604 / 678 | 89.1% |
| fundamentals (any field) | 604 / 678 | 89.1% |
| company_ratios (any field) | 604 / 678 | 89.1% |
| news_sentiment (latest) | 625 / 678 | 92.2% |

## 5 · ESG integration decision

- ESG coverage: 234/678 = **34.5%** — below the 50% threshold for a meaningful factor.
- ESG distinct dates: **1** — single-snapshot would introduce look-ahead bias on a historical backtest.
- **Decision**: not integrated into the core factor composite.  Rationale matches CW1 §2.4.  Opt-in `--esg-screen` remains available for comparison.

## 6 · Verdict

✅ CW1↔CW2 integration is **live and contract-valid**.  CW2 reads the CW1 schema in-place — no data duplication, no schema drift.

## 7 · v0.3.0 Addendum — factor-set change

After the post-audit IC diagnostic (see `../AUDIT_FINDINGS_MATRIX.md` and
`../FACTOR_REVIEW_2026-04-22.md`) CW2 reduced the composite from the
original CW1 4-factor specification (momentum / value / quality /
sentiment at 0.30 / 0.30 / 0.25 / 0.15) to a 2-factor composite
(momentum / value at 0.50 / 0.50).

**Rationale (empirical, not organisational):**
- **Sentiment factor** — CW1 `news_sentiment` table has a single
  2026-03-20 snapshot; the Mongo article collections
  (`ift_cw1.news_sentiment`, `ift_cw1_sentiment.raw_news_articles`) hold
  fewer than 10 articles per month before 2025-11 rising to ~4,800/month
  by 2026-03, with 512 stocks in the universe — insufficient per-stock
  coverage for ~90 % of the 32-month backtest.  Dropping sentiment is
  structural, not a CW2 code change.
- **Quality factor** — the `_pick_ratio` fallback chain was landing on
  1-snapshot columns (`earnings_stability`, `debt_to_equity_inv`) whose
  fallback formulas were economically incorrect.  After switching to the
  400+-snapshot `_hist` variants (`roe_hist`, `debt_to_equity_hist`,
  `profit_margin_hist`) the resulting IC is `-0.0175, t = -1.95,
  p = 0.061` — nearly-significant *negative*.  The fix didn't rescue
  quality; it made a genuine "junk rally" pattern in the 2023-2026 sample
  measurable.

CW1 tables were not modified.  The change is isolated to
`config/backtest_config.yaml` (`factors.base_weights`) and the fix to
`engine/factors.py::compute_quality`.  All four factors are still
computed and surfaced in `factor_ic.parquet` for the diagnostic report
exhibit.
