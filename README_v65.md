# CW2 Strategy — Kolmogorov Team

## Final Deliverables

### Reports
- `CW2_v4_Report_EN/CN.docx` — Market-neutral research baseline (10 sections, Net Sharpe +0.07)
- `CW2_v6_Report_EN/CN.docx` — **Final strategy** (10 sections, Net Sharpe +0.649)

### Code (reproducible)
```bash
# Final strategy (v6.5 Long-Only + Risk Overlay)
cd v6_long_biased && python run_strategy.py
# Output: output/monthly_returns.csv

# Market-neutral baseline (v4)
cd v4_alpha_engine && python run_strategy.py
```

### Prerequisites
```bash
pip install pandas numpy scipy pyyaml vaderSentiment
```

## Final Strategy: v6.5 Risk-Managed Long-Only

| Metric | Value |
|--------|-------|
| Net Sharpe | **+0.649** |
| Net Annual Return | +12.3% |
| Net Total Return | +58.8% |
| Max Drawdown | -6.5% |
| Beta | +0.67 |
| IS Net Sharpe | +0.31 |
| OOS Net Sharpe | +1.31 |
| S&P 500 Total | +45.8% (same holding periods) |

**Factors**: Momentum (12-1) + Value (B/P+E/P+CF/P), z-score weighted
**Construction**: Score × inverse-volatility, max 1.5% per stock
**Risk Overlay**: 3-signal regime (S&P 200DMA, 12M return, VIX percentile)
**Rebalancing**: Bi-monthly, PIT lag 45 days, 10bps trading cost

## Market-Neutral Baseline: v4

Net Sharpe +0.068, Beta -0.095 (approximately market-neutral).
Demonstrates that strict market-neutral with PIT constraints produces near-zero alpha.
Documented as honest research finding — the short leg fails in structural bull markets.

## Data

Tamer CW1 pipeline PostgreSQL export (matches CW1 Report Table 8):
- 604 tickers, 168K fundamental rows, 259K ratio rows
- Real FRED DGS3MO risk-free rate, 5 benchmark indices
- Our sentiment: 8,216 rows via Google News RSS + VADER (semi-annual)

## Research Journey

v2 → v3 → v4 → v5 → v6: progressive audit corrections (PIT lag, FX, dedup,
B/P CF/P fix, annualization, max weight). Initial Sharpe ~1.0 declined to v4's
+0.07 as false alpha was removed. v6 pivoted to long-only, reaching +0.649.

## Team
Kolmogorov Team — MSc Banking and Digital Finance, UCL, 2026
