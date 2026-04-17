# CW1 ↔ CW2 Integration Validation Report
*Generated 2026-04-17*
**DB:** `postgres@localhost:5439/fift` — schema `systematic_equity`
**CW2 config hash:** `aae3fc77929786c0` · **git:** `cb823727d75d18765b1bca3a0e8146882f69cbda`
**CW1 data snapshot SHA-256:** `4c93491f90c587762b55ffc6a8154455faf3ecb7dbda40aa7295ac25389c5f24`

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
