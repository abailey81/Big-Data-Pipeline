# Factor review — sentiment & quality decision (FINAL, 2026-04-22)

**TL;DR (updated after construction-fix measurement):**
- **Sentiment: dropped.** Mongo investigation confirmed no usable historical
  article data (< 10 articles/month across the whole universe for 90% of the
  backtest window); literature argues monthly frequency is the wrong horizon.
- **Quality: dropped** after I fixed the construction bug. The fresh IC with
  proper `_hist` time-series columns is **−0.0175 with t = −1.95, p = 0.061**
  — i.e. quality is nearly-significantly *negative* in this sample (a "junk
  rally" pattern across 2023-2026).  Construction fix didn't rescue it; it
  made the true anti-alpha signal visible.  Honest drop.
- **New default: 2-factor momentum + value (0.50 / 0.50).** Fresh Sharpe
  1.32 vs 4-factor 1.03, max DD similar, β closer to zero.  All other fixes
  (weight cap, empirical β, trade ledger, HRP, long/short legs) still intact.

---

## Sentiment — DROP

### What the data actually says

| Metric | Value |
|---|---|
| `news_sentiment` table coverage | 625 rows, all dated 2026-03-20 (single snapshot) |
| Sentiment IC (Spearman), 32 monthly rebalances | 0.0000 — **identically zero for every month** |
| Reason | PIT filter `cob_date ≤ rebalance_date` returns **no rows** for every pre-2026-03-20 rebalance → every stock gets the same (zero / NaN) sentiment score → Spearman rank correlation to forward returns is zero by construction |
| Ablation Sharpe (pre-fix) | full = 0.94 → no_sentiment = 0.97 (barely moves) |
| Fama-MacBeth t-stat | `+nan` (undefined — σ = 0) |

Lucian's 8,216-row semi-annual VADER panel would *in principle* fix the PIT
coverage gap, but the empirical conclusion he already reported is the same:
**no predictive IC at monthly frequency**. This matches the literature
(Tetlock 2007; Da, Engelberg & Gao 2011) — sentiment shocks decay on
daily-to-weekly horizons; monthly aggregation arbitrages them away.

### Action

- Set `factors.base_weights.sentiment = 0.00` in `config/backtest_config.yaml`.
- Re-normalise the remaining three to `0.35 / 0.35 / 0.30` (mom / val / qual)
  or `0.50 / 0.50` if we also drop quality (see below).
- Sentiment factor **construction stays in the codebase** so the report can
  document the null finding with a full audit trail (PLAN §14 P5 — "negative
  results are published").
- `analytics/ablation.py` already runs a `no_sentiment` variant; the report
  table will surface this number directly.

---

## Quality — I fixed the construction bug; now we can decide on data

### What was actually happening

Three of the sub-factors in the quality composite were falling through to
broken fallbacks:

| Sub-factor | CW1 column used | Snapshots | Result |
|---|---|---|---|
| ROE | `roe_hist` (second priority) | **433** ✓ | OK — the `roe_computed` first priority dropped out of PIT, but the chain correctly fell through |
| Earnings stability | `earnings_stability` | **1** (2026-03-20) | Always absent pre-2026-03-20 → fell back to `1/rank(|EPS|)` which is economically *backwards* (small EPS = "stable") |
| Inverse D/E | `debt_to_equity_inv` | **1** | Always absent → fell back to `eq/(|debt|+0.01·|eq|)` with an arbitrary 0.01 offset that blows up at zero debt |

End result: mean Spearman IC = −0.0005, IR = −0.008, t = −0.04, **p = 0.965** — noise.

### The fix I pushed

Updated `compute_quality` in [engine/factors.py](engine/factors.py) to
prefer the 400+-snapshot `_hist` variants CW1 actually carries:

- ROE → `roe_hist` (433 snapshots) as first priority
- Inverse D/E → `1 / (|debt_to_equity_hist| + 0.1)` (433 snapshots, 0.1 ≈ 1st-quartile D/E)
- Earnings-stability proxy → `profit_margin_hist` (431 snapshots — the published QMJ "profitability" sub-factor per Asness-Frazzini-Pedersen 2019 §III.A)

Probe IC at 6 sample dates (trailing-21d return proxy — true forward IC
needs the backtest re-run):

```
2024-01-31  IC = +0.108  p = 0.015
2024-06-28  IC = +0.039  p = 0.377
2024-12-31  IC = -0.104  p = 0.019
2025-03-31  IC = -0.010  p = 0.828
2025-09-30  IC = +0.067  p = 0.131
2026-01-31  IC = +0.023  p = 0.604
Mean (proxy): +0.020
```

Still noisy. Not a slam-dunk. But the revised factor has **508–512 stocks
per date with real dispersion (std 0.82–1.14)** — unlike the previous
zero-signal fallback output.

### Proposed ablation grid for final decision

`analytics/ablation.py::ABLATION_VARIANTS` now runs **eight** variants so
we can see everything on one parquet:

| Variant | Momentum | Value | Quality | Sentiment |
|---|---|---|---|---|
| `full_4factor` | 0.30 | 0.30 | 0.25 | 0.15 |
| `no_momentum` | 0.00 | 0.44 | 0.35 | 0.21 |
| `no_value` | 0.44 | 0.00 | 0.35 | 0.21 |
| `no_quality` | 0.40 | 0.40 | 0.00 | 0.20 |
| `no_sentiment` | 0.35 | 0.35 | 0.30 | 0.00 |
| **`no_sentiment_3factor`** | **0.35** | **0.35** | **0.30** | **0.00** |
| **`mom_val_only`** | **0.50** | **0.50** | **0.00** | **0.00** |
| **`mom_val_qual`** | **0.40** | **0.40** | **0.20** | **0.00** |

The three new rows let us directly compare:
- Old (4-factor) vs. no-sentiment (3-factor)
- 3-factor with fixed quality vs. 2-factor (drop quality entirely)
- 3-factor with old-default 0.40/0.40/0.20 vs. variant weightings

After the CPCV run completes (in ~35 min), I'll trigger the ablation
re-run with the fixed quality code. Numbers will be in the fresh
`output/ablation_results.parquet` by the end of the afternoon.

---

## Empirical evidence from current run (for context)

### Factor IC (pre-quality-fix)

| Factor | mean IC | IR | t | p | % positive |
|---|---|---|---|---|---|
| **Momentum** | **+0.0645** | **+0.43** | **+2.44** | **0.021** | **65.6%** |
| Value | +0.0158 | +0.18 | +1.00 | 0.325 | 59.4% |
| Quality (broken) | −0.0005 | −0.01 | −0.04 | 0.965 | 59.4% |
| Sentiment | 0.0000 | n/a | n/a | n/a | n/a |

Only **momentum** is significant at conventional levels. Value is
economically plausible but statistically weak. Quality as currently
constructed is noise. Sentiment is structurally zero.

### Ablation Sharpe (pre-fix, from `ablation_results.parquet`)

| Variant | Sharpe | Δ from full |
|---|---|---|
| no_quality | **+1.499** | **+0.557** |
| no_sentiment | +0.970 | +0.027 |
| full_4factor | +0.943 | 0.000 |
| no_value | +0.382 | −0.560 |
| no_momentum | +0.101 | −0.842 |

Removing quality *improves* Sharpe by +0.56 — the strongest ablation
signal we have. That's the empirical case for dropping it.

---

## My position — FINAL (updated with empirical numbers)

- **Sentiment: drop.** Fully agreed with Lucian.  Investigated every
  accessible source (PG table, both Mongo collections, Lucian's panel) —
  no usable historical data for ~90% of the backtest window.
- **Quality: drop.**  The construction fix was correct and necessary, but
  it doesn't save the factor.  With the `_hist`-based construction we get:
  - Full sample IC = −0.0175, t = −1.95, p = 0.061 (nearly-significant
    *negative*)
  - Normal-VIX regime IC = −0.0292, t = −2.02 (n = 16, significant negative)
  - 8 of 11 calendar quarters: negative IC
  - Ablation: full_4factor Sharpe 0.94 → no_quality Sharpe 1.50
  The 2023-2026 sample is a "junk rally" period where high-margin /
  low-leverage / high-ROE names systematically underperformed.  QMJ is a
  well-established factor, so we don't flip the sign — we drop and document
  the sample-period anomaly honestly in Report §7.  (Flipping to a
  low-quality factor would look like data mining and contradict the
  Vayanos-Woolley pillar rationale.)

## What the final run actually delivered

Pre-decision 4-factor → 2-factor (same 32-month window):

| Metric | 4-factor | 2-factor | Δ |
|---|---|---|---|
| Dynamic Net 20bp Sharpe | 1.027 | **1.316** | +0.289 |
| Dynamic Net 20bp ann. return | 11.89% | **15.74%** | +3.85pp |
| Static  Net 20bp Sharpe | 0.967 | **1.418** | +0.451 |
| HRP Net 20bp Sharpe | 1.537 | **1.592** | +0.055 |
| HRP Net 20bp max drawdown | −8.46% | **−2.67%** | +5.79pp improvement |
| Portfolio β (mean) | +0.125 | +0.083 | closer to neutral |

All previously-fixed engine issues still pass their acceptance criteria
on the fresh 2-factor run: max single-stock weight 0.05 exact, portfolio_β
empirical, trade_ledger 2,171 rows, HRP 32/32 populated, long_leg and
short_leg 32/32 non-zero.

## Ablation grid — final numbers (8 variants, fixed quality construction)

| Variant | Weights (mom / val / qual / sent) | Sharpe | Δ from full_4factor | Max DD |
|---|---|---:|---:|---:|
| **no_quality** | 0.40 / 0.40 / 0.00 / 0.20 | **+1.418** | **+0.456** | −7.97% |
| **mom_val_only** | 0.50 / 0.50 / 0.00 / 0.00 | **+1.418** | **+0.456** | −7.97% |
| no_sentiment / no_sentiment_3factor | 0.35 / 0.35 / 0.30 / 0.00 | +0.983 | +0.021 | −8.20% |
| full_4factor (CW1 default) | 0.30 / 0.30 / 0.25 / 0.15 | +0.962 | 0.000 | −8.21% |
| mom_val_qual | 0.40 / 0.40 / 0.20 / 0.00 | +0.901 | −0.061 | −8.66% |
| no_value | 0.44 / 0.00 / 0.35 / 0.21 | +0.480 | −0.482 | −11.25% |
| no_momentum | 0.00 / 0.44 / 0.35 / 0.21 | −0.136 | −1.098 | −11.37% |

**Reading of the table**
- **`no_quality` and `mom_val_only` tie at Sharpe 1.418** — mathematically
  equivalent because sentiment weight in `no_quality` contributes zero
  signal, so after renormalisation both collapse to a 0.50 / 0.50
  momentum / value book.
- **Adding quality at any weight hurts**: mom_val_qual (qual = 0.20) is
  the worst non-destructive variant.  Consistent with the fresh
  IC = −0.0175, t = −1.95 finding.
- **Dropping sentiment is free**: `no_sentiment` (+0.021 Sharpe) is
  essentially indistinguishable from `full_4factor` because sentiment
  had IC = 0 throughout.  The uplift comes entirely from removing
  quality.
- **Momentum and value are both real**: `no_momentum` collapses the
  strategy; `no_value` cuts Sharpe by nearly half even though value's
  IC t-stat is only 1.00 in isolation.

## CPCV sensitivity — still running

`analytics/sensitivity.py::run_sensitivity_cpcv` — 15 γ × λ × 66 CPCV
folds = 990 rows expected.  Will populate
`output/sensitivity_grid.parquet` with deflated-Sharpe-adjusted metrics
per fold for the Report §4.4 heatmap.  ETA ~30 more minutes.

Monte Carlo (10,000 paths) + regime performance already done on the
fresh 2-factor outputs.

## Report narrative this supports

Section 4.3 can now say: "The CW1 four-factor composite was evaluated on
32 monthly OOS rebalances.  Momentum showed a significant IC
(t = 2.44, p = 0.02).  Value was economically plausible but statistically
weak (t = 1.00, p = 0.33). Quality exhibited a nearly-significant
**negative** IC after a construction correction, consistent with the
observed 'junk rally' dynamic of the 2023-2026 sample.  Sentiment IC was
structurally zero because historical news-article coverage is
concentrated in the last three months of the sample.  On these grounds,
and supported by the factor-ablation table (§5.1 —
`full_4factor` Sharpe 0.94 → `mom_val_only` Sharpe X.XX), the integrated
strategy reduces to a two-factor momentum + value composite."

This is the *right* story for the report: not "we got a 0.94 Sharpe
somehow"; but "we diagnosed each factor empirically, fixed the
construction bugs, and reduced the composite to what the data actually
supports".

Thanks for the push.  Ablation + CPCV numbers will be in
`output/ablation_results.parquet` and `output/sensitivity_grid.parquet`
once those jobs finish.

— Tamer
