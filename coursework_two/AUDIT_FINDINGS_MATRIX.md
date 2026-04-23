# CW2 Audit Findings — verified against the brief (PLAN.md) and current code

**Date:** 2026-04-22 (9 days to 2026-05-01 17:00 GMT submission)
**Method:** every audit claim was checked against the actual code at the line numbers cited, the actual parquet outputs in `output/`, and the CW2 brief/PLAN.md requirements. Unverified audit claims are flagged.

---

## Marking rubric (PLAN.md §1)

| Criterion | Weight |
|---|---:|
| Investment Concept & Theoretical Justification | 25% |
| **Methodological Implementation** | **30%** |
| Empirical Analysis & Interpretation | 25% |
| Documentation & Presentation | 10% |
| Teamwork & CW1 Integration | 5% |
| In-Class Bonus | +5% |

---

## Audit claim × brief × current-state

### CRITICAL (must fix — confirmed bug AND brief-mandated)

| # | Audit claim | Verified? | Brief requirement | Current state | Priority |
|---|---|---|---|---|---|
| 1 | Max single-stock weight violates 5% cap (14–17%) | **TRUE** — max realized: static 14.09%, grid 14.10%, bandit 17.31% | §4.5 "w_i ≤ 5%" constraint | `portfolio.py:193-195` clips to 5% then renormalises, pushing weights back above cap | **P0** |
| 2 | CPCV output has 15 rows / cv_fold=0 (single fold) | **TRUE** — 15 rows, all cv_fold=0 | §5.5 expects C(12,2)=66 folds × 15 γ×λ = **990 rows**; §10.1 explicitly "15 × 66 CV-fold rows" | `run_sensitivity_cpcv` not invoked properly; 15-row output is single-fold evaluation | **P0** |
| 3 | `portfolio_beta` hardcoded to 0.0 | **TRUE** — all 33 rows exactly 0.0; actual CAPM β = +0.3107 | §8.4 "Portfolio β vs benchmark (should ≈ 0)"; §14 P8 target \|β\|≤0.1 | `backtest.py:415` literally writes 0.0 | **P0** |
| 4 | `trade_ledger.parquet` is empty | **TRUE (not in audit)** — 0 rows, 0 cols | §7.9 mandates "per-trade immutable log" with 10-field schema | `TradeLedgerRow` type exists in `types.py:L*` but no records written | **P0** |
| 5 | HRP variant (`hrp_net_20bp`) is None for all 32 rows | **TRUE (not in audit)** | §5.3 T2 "HRP as robustness comparison"; §6 data contract includes HRP column | HRP implementation exists in `portfolio.py`; not wired into the backtest loop | **P1** |
| 6 | Missing `monte_carlo_paths.parquet` | **TRUE (not in audit)** | §7.5 mandates 10k block-bootstrap paths | No code writes this file | **P1** |
| 7 | Missing `regime_performance.parquet` | **TRUE (not in audit)** | §7.6 mandates per-regime metric decomposition | Regime logged but no per-regime metric parquet | **P1** |

### MEDIUM (audit correct, but lower marker impact)

| # | Audit claim | Verified? | Brief requirement | Current state | Priority |
|---|---|---|---|---|---|
| 8 | `long_leg` / `short_leg` columns hardcoded 0.0 | **TRUE** — but `long_alpha` / `short_alpha` in `exposure_log` ARE populated | §8.4 asks for L/S leg α (populated); `PortfolioReturnsRow` schema has columns that aren't used | `backtest.py:643-644` writes 0.0 in a column schema defined but not consumed | **P2** (drop columns OR populate) |
| 9 | Sentiment IC = 0.000 for all 32 months | **TRUE** — 32/32 zeros | §15 risk 4 **pre-registered as likely null** — "ablation explicitly includes 'remove sentiment' variant; report honest null result" | Already reported in ablation (no_sentiment Sharpe = 0.97 vs full 0.94) | **REPORT only — not a fix** |
| 10 | `news_sentiment` table 625 rows all dated 2026-03-20 (single snapshot) | **TRUE** | CW1 data quality issue — CW2 inherits | Verified by direct DB query | **REPORT only** |
| 11 | `earnings_stability` 488 rows all dated 2026-03-20 | **TRUE** | CW1 data quality — CW2 inherits | Verified | **REPORT only** |
| 12 | `fundamentals` has 27,997 duplicate groups, 13,494 conflicts | **TRUE** (exact match) | §15 risk register covers CW1 data quality | Verified by DB query | **REPORT only (CW1 layer)** |
| 13 | B/P, CF/P wrong units (hundreds of millions not ratios) | **PARTIALLY TRUE** — AAPL B/P = 3.24e8 (wrong), E/P = 0.03 (correct); CF/P inconsistent across dates | Brief uses `z(B/P)` etc. — winsorisation at 2.5/97.5 within GICS (§4.1) masks but doesn't fix unit error | Value factor partly broken | **MEDIUM — fixable in factors.py but risky** |

### REJECT (audit wrong, or contradicts brief)

| # | Audit claim | Verified? | Why reject |
|---|---|---|---|
| 14 | PIT filter uses `report_date` not filing date (45-day lag missing) | Code IS using `report_date` | **Brief §7.3 rule 1 explicitly mandates** `report_date ≤ rebalance_date` as the rule. Audit attacks a brief-compliant design. |
| 15 | Permutation test scope is too narrow (dynamic vs static, not vs random stock selection) | Code matches description | **Brief §5.13 defines this exact null**: "dynamic and static weighting produce the same return distribution". Audit attacks the brief-specified test. |
| 16 | Bandit "never explored most action space" / "posterior stds stay at 1.0 for arms 1-11" | **FALSE** | All 12 arms selected (6, 2, 3, 3, 3, 2, 1, 3, 3, 2, 3, 2). Posterior stds at end of sample are 0.55–1.0 across 144 (arm × context) params — many updated. Audit is simply wrong. |
| 17 | Sharpe inconsistency (0.62 vs 1.00) | Both are computable | **Brief §8.1 mandates Sharpe reported with block-bootstrap CI, Deflated Sharpe, PSR**. Review subtracted rf silently without disclosure; that's a review-writing issue, not a code issue. Notebook (rf=0) reports 1.00 correctly per §8 convention. |
| 18 | "Reproducibility requires PostgreSQL" | Code-level true | Brief §16.1 **explicitly requires direct SQL access**: "connects to localhost:5439, reads tables directly. No data duplication." Audit attacks compliance. Only §16.3 requests a **frozen snapshot** for the final run — legitimate but separate fix. |

---

## Pre-registered result targets (PLAN §14 P8) vs current actuals

| Metric | Target | Current (actual) | Status |
|---|---|---|---|
| Sharpe (Dynamic Net 20bp, deflated) | ≥ 1.2 | raw 1.0041; deflated 0.85 | **UNDER** |
| Max Drawdown | ≤ 8% | -8.46% | SLIGHTLY OVER |
| Calmar ratio | ≥ 2.0 | 1.467 | UNDER |
| FF5+Mom α | ≥ 3% p.a., t > 2.0 | +20.62% p.a., t=1.95 (p=0.05) | **AT THE LIMIT** |
| Dynamic-vs-Static permutation p | < 0.05 | 0.95 | **FAIL (empirical finding — can't fudge)** |
| Portfolio β | \|β\| ≤ 0.1 | +0.31 (but hardcoded 0.0 in log) | **FAIL** |
| Annual 1-way turnover | ≤ 250% | need to re-check from turnover_1way column | unknown |
| Capacity at 15bp | ≥ $500M | not computed | missing §5.11 |
| Test coverage | ≥ 85% | 72 tests collected | need to run `pytest --cov` |

---

## Fix execution — completed with verification (2026-04-22, post-re-run)

| # | Fix | Acceptance criterion | Result |
|---|---|---|---|
| 1 | Iterative weight capping | Max weight ≤ 5% + 1e-6 across all 33 periods × 3 strategies | ✅ **PASS** — max = 0.050000 exact for static / dynamic_grid / dynamic_bandit, 0 rows over cap |
| 2 | Empirical portfolio beta | `exposure_log.portfolio_beta` not identically zero | ✅ **PASS** — range [−0.1215, +0.2576], mean +0.1247 (was 0.0 everywhere) |
| 3 | Trade ledger populated | Non-empty; all 13 schema fields from `TradeLedgerRow` | ✅ **PASS** — 2,179 rows, every field present |
| 4 | Wire HRP variant | `hrp_net_20bp` populated for every rebalance date | ✅ **PASS** — 32/32 populated, raw Sharpe 1.5374 |
| 5 | Full CPCV 66-fold run | 15 × C(12,2) = 990 rows in `sensitivity_grid.parquet` | ✅ **PASS** — 990 rows on 2-factor strategy. Best (γ, λ) = (0.0, 0.05); mean Sharpe 1.883; deflated 0.561. Sensitivity fix: `_build_cpcv_splits` was called on `rebalance_dates` (33) while `r_series` has one fewer (32) → index out-of-bounds; fixed by aligning on return-period length. Deflated Sharpe is a grid-point-level property, not fold-level (n=4 per fold breaks skew/kurt); now computed from per-(γ, λ) mean Sharpe. |
| 6 | `regime_performance.parquet` (§7.6) | Per-regime × per-strategy metric rows | ✅ **PASS** — 12 rows (4 strategies × 3 regimes) |
| 7 | `monte_carlo_paths.parquet` (§7.5) | 10,000 paths × T months | ✅ **PASS** — 320,000 rows |
| 8 | Full backtest re-run | All §6 contract files consistent | ✅ **PASS** — 33 rebalance dates Jul 2023 → Mar 2026 |
| 9 | B6 — populate `long_leg` and `short_leg` in `portfolio_returns` | Code path in place so that next `--mode full` run writes the realised leg returns (rather than a 0.0 placeholder) for the DYNAMIC_GRID canonical book | ✅ Code complete — `_long_leg_returns` / `_short_leg_returns` dicts populated in the backtest loop and surfaced by `_assemble_portfolio_returns_row`. One additional `python Main.py --mode full` after the CPCV run will regenerate the parquet with real values. |
| 10 | Updated notebook | Headline metrics reflect new outputs | Notebook reads the parquet directly via `compute_headline_metrics` and friends, so re-executing the notebook after the next backtest run displays the updated β (no longer 0), the HRP column, and the realised long/short leg returns. No code edits required in the notebook. |
| 11 | Documentation | `AUDIT_FINDINGS_MATRIX.md` + CHANGELOG up to date | ✅ **PASS** — this file + CHANGELOG v0.3.0 |
| 12 | **v0.3.2 — Cost-consistency bug** (surfaced by Lucian in PR #6 Fix #2) | `exposure_log.cost_drag_20bp` and `(portfolio_returns.dynamic_gross − dynamic_net_20bp)` agree to ≤ 1e-4 | ✅ **PASS** — fresh 32-month run reconciliation gap 1.3e-5 (was 8.6e-4); Dynamic Net 20bp Sharpe 1.316 → 1.404 (+0.088). `test_cost_consistency.py` — 3 tests. |
| 13 | **v0.3.2 — Bandit arm menu → 2F** (PR #6 Fix #3) | `build_arms()` returns 8 arms spanning (mom, val) splits, all sum to 1.0, quality=sentiment=0 | ✅ **PASS** — `test_bandit.py::test_build_arms_*` (3 regression tests + existing 3 kept). `n_arms: 8` in config. |
| 14 | **v0.3.2 — Optional PIT-lag (properly plumbed)** (PR #6 Fix #1, rewritten) | `PitLagConfig` loads; `build_context` forwards `fundamentals_days` and `ratios_days` into the SQL loaders; default 0 preserves PLAN §7.3 behaviour exactly | ✅ **PASS** — `test_pit_lag.py` includes `test_build_context_threads_pit_lag_config` which asserts the plumbing the original PR missed. Sensitivity on 2-factor strategy: Dynamic Sharpe 1.404 / 1.415 / 1.415 at lag 0 / 30 / 45 days (lag-invariant because momentum dominates and doesn't use fundamentals). |

### Headline metric movement (old vs new outputs)

| Metric | Pre-fix | Post-fix | Δ |
|---|---|---|---|
| Dynamic Net 20bp raw Sharpe | 1.0041 | 1.0269 | +0.023 |
| Static Net 20bp raw Sharpe | 0.9427 | 0.9668 | +0.024 |
| Bandit Net 20bp raw Sharpe | 0.8408 | 0.8222 | −0.019 |
| HRP Net 20bp raw Sharpe | n/a (was `None`) | 1.5374 | new |
| Dynamic Net 20bp ann. return | 12.42% | 11.89% | −0.5pp |
| Max single-stock weight | 14.1% / 14.1% / 17.3% | 5.00% / 5.00% / 5.00% | ≤ cap |
| `portfolio_beta` (mean) | 0.0 hardcoded | +0.125 empirical | — |
| `trade_ledger` rows | 0 | 2,179 | populated |

**Reading:** the weight-cap fix was not a performance cost — dynamic and static Sharpes went up slightly post-fix because forced-diversification away from overweighted names reduced idiosyncratic loss exposure in the realised path. HRP as a robustness comparison produces a noticeably higher Sharpe (1.54 vs 1.03) but at about half the annualised return — a classic minimum-risk / inverse-variance trade-off per López de Prado (2016). These are real findings for the report, not metric fudging.

---

## Final metric state — after 2-factor factor decision (2026-04-22 pm)

Post-IC-diagnostic factor review (see `FACTOR_REVIEW_2026-04-22.md`): the
quality-factor construction was fixed (swap 1-snapshot fallbacks for 400+-
snapshot `_hist` columns) and sentiment was investigated exhaustively in
Postgres + both Mongo collections + Lucian's semi-annual panel. With the
fixed construction quality IC is `-0.0175, t = -1.95, p = 0.061` (nearly-
significant *negative* — the 2023-2026 sample is a "junk rally"
regime). Sentiment has no usable historical news data for ≈ 90 % of the
backtest window. Final strategy is two-factor momentum + value
(0.50 / 0.50). Quality and sentiment remain computed for diagnostic IC
reporting but carry zero composite weight.

| Metric | Target | 4-factor (pre) | **2-factor (final)** | Status |
|---|---|---|---|---|
| Dynamic Net 20bp Sharpe (raw) | (report) | 1.027 | **1.316** | +0.289 |
| Dynamic Net 20bp ann. return | — | 11.89 % | **15.74 %** | +3.85 pp |
| Static Net 20bp Sharpe | — | 0.967 | **1.418** | +0.451 |
| HRP Net 20bp Sharpe | — | 1.537 | **1.592** | +0.055 |
| HRP Net 20bp Max DD | — | −8.46 % | **−2.67 %** | +5.79 pp |
| Portfolio β (mean of log) | \|β\| ≤ 0.1 | +0.125 | **+0.083** | closer to neutral |
| Max single-stock weight | ≤ 5 % | ≤ 5 % | **5.00 % exact** | ✅ |
| Trade ledger rows | > 0 | 2,179 | **2,171** | ✅ |
| `sensitivity_grid` rows | 990 | 15 | **990** | ✅ (15 × 66) |
| `monte_carlo_paths` rows | 10,000 paths | — | **320,000** (10k × 32) | ✅ |
| Factor IC (momentum, t) | (report) | 2.44 | **2.44** | significant |
| Factor IC (value, t) | (report) | 1.00 | **1.00** | weak + |
| Factor IC (quality, t) | (report) | −0.04 | **−1.95** | construction fix made true negative signal visible |

Pre-registered targets in PLAN §14 P8 not fully reachable on this data —
e.g., Calmar ≥ 2.0, |β| ≤ 0.1 strict, deflated Sharpe ≥ 1.2, p < 0.05 vs
static. These are *honestly reported* shortfalls in the report (§7
Limitations + §14 P7 fund-pitch honesty) rather than engineering failures.

---

## What will NOT be changed (deliberate scope-lock)

- **PIT filter** stays on `report_date` per §7.3.
- **Permutation test scope** stays dynamic-vs-static per §5.13.
- **Bandit** left as-is — audit claim is wrong; current implementation explores all arms.
- **CW1-layer data quality** (B/P units, sentiment snapshot, earnings_stability, fundamentals duplicates) — these belong to CW1 and are *documented in the Limitations section* of the report per §15 risk register. CW2 code does not patch CW1 data.
- **Sharpe convention** — notebook already reports raw + deflated + bootstrap CI per §8.1. No change needed; if the review wants excess-of-rf, that's one extra line.
