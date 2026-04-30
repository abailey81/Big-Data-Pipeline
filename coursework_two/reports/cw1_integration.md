# CW1 ↔ CW2 Integration Validation

**DB:** `postgres@localhost:5439/fift` — schema `systematic_equity`.
Run timestamp and reproducibility hashes (config, data snapshot, git
SHA) are stamped in `output/backtest_metadata.parquet` for the run that
produced the headline figures cited in the report.

## 1. Schema contract

Every CW1 table read by CW2 carries the expected columns; if CW1 ever
drops or renames one, the engine raises at load time.

| Table | Expected columns | Status |
|---|---|---|
| `company_static`   | country, gics_sector, security, symbol           | OK |
| `daily_prices`     | adj_close_price, cob_date, currency, symbol, volume | OK |
| `fundamentals`     | field_name, field_value, period_type, report_date, symbol | OK |
| `company_ratios`   | field_name, field_value, snapshot_date, symbol   | OK |
| `fx_rates`         | close_rate, cob_date, currency_pair              | OK |
| `vix_data`         | close_price, cob_date                            | OK |
| `risk_free_rate`   | cob_date, rate_pct                               | OK |
| `benchmark_index`  | adj_close_price, cob_date, symbol                | OK |
| `news_sentiment`   | cob_date, sentiment_score, symbol                | OK |

## 2. Row counts and freshness (validation snapshot)

| Table | Row count | Latest date | Distinct symbols |
|---|---:|---|---:|
| `company_static`   | 678     | n/a        | 678 |
| `daily_prices`     | 948,403 | 2026-04-15 | 604 |
| `fundamentals`     | 195,910 | 2026-03-20 | 604 |
| `company_ratios`   | 250,788 | 2026-03-20 | 604 |
| `fx_rates`         | 6,254   | 2026-04-14 | 4   |
| `vix_data`         | 1,506   | 2026-03-19 | 1   |
| `risk_free_rate`   | 1,563   | 2026-03-18 | 1   |
| `benchmark_index`  | 7,526   | 2026-03-19 | 5   |
| `news_sentiment`   | 625     | 2026-03-20 | 625 |

## 3. Currency-inference parity (CW1 `ticker_utils.infer_currency`)

| Symbol  | Expected | Inferred | OK |
|---|---|---|---|
| `BARC.L`   | GBP | GBP | OK |
| `BNP.PA`   | EUR | EUR | OK |
| `SAP.DE`   | EUR | EUR | OK |
| `ADS.DE`   | EUR | EUR | OK |
| `SAN.MC`   | EUR | EUR | OK |
| `RBC.TO`   | CAD | CAD | OK |
| `NOVN.S`   | CHF | CHF | OK |
| `NESN.SW`  | CHF | CHF | OK |
| `AAPL`     | USD | USD | OK |

## 4. Factor-coverage breakdown

| Source | Symbols covered | % of universe |
|---|---:|---:|
| prices (daily_prices)         | 604 / 678 | 89.1 % |
| fundamentals (any field)      | 604 / 678 | 89.1 % |
| company_ratios (any field)    | 604 / 678 | 89.1 % |
| news_sentiment (latest)       | 625 / 678 | 92.2 % |

## 5. ESG integration decision

- Coverage 234 / 678 = 34.5 % — below the 50 % threshold a meaningful
  factor would need.
- Distinct dates: 1 — a single-snapshot column would introduce
  look-ahead bias on a historical backtest.
- Decision: not integrated into the composite.  An opt-in
  `--esg-screen` flag remains available for comparison runs.

## 6. Factor-set change (Coursework 2 implementation)

The implemented composite reduces the Coursework 1 four-factor proposal
to two factors (momentum + value at 50/50) on the basis of out-of-sample
information-coefficient evidence.  Full discussion in the report
(§§1.2, 2.2.1, 4.2).

- Sentiment IC = 0.0000 across all 32 monthly rebalances because the
  `news_sentiment` table contains only a 2026-03-20 snapshot, so the
  PIT cutoff returns no rows for any pre-2026-03-20 rebalance.  The
  Mongo article collections also concentrate post-2025-11, leaving
  ≈ 90 % of the backtest window without per-stock news coverage.
- Quality IC = −0.0175 (t = −1.95, p = 0.061) after `compute_quality`
  was switched to the 400+-snapshot `_hist` ratio variants
  (`roe_hist`, `debt_to_equity_hist`, `profit_margin_hist`) — an
  economically defensible QMJ proxy.  The negative point estimate
  reflects the sample-period junk rally documented in §6.4 of the
  report.

CW1 tables are not modified.  The change is isolated to
`config/backtest_config.yaml::factors.base_weights` and the fix in
`engine/factors.py::compute_quality`.  All four factors remain computed
and surfaced in `factor_ic.parquet` for the diagnostic IC exhibit.
