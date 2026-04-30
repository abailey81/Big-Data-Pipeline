# Changelog

All notable changes to the CW2 backtest engine.  Format follows
[Keep a Changelog](https://keepachangelog.com/), semantic versioning.

## [0.3.2] — 2026-04-30

### Fixed

- `analysis/run_attribution_ls.py` was passing `(strategy − CW1 rf_rate)`
  to `run_ff5_mom_regression`, which then internally subtracted FF5's RF
  again — a double-subtraction that pulled the alpha estimate downward.
  Script now passes raw strategy returns and uses month-end alignment so
  the FF5 join keeps the last sample observation.  Annualised alpha is
  reported on the geometric `(1+α_m)^12 − 1` convention to match the
  report's headline figure.
- `analysis/run_inference_ls.py` was bootstrapping raw Sharpe; the
  reported confidence intervals are on excess Sharpe.  Now passes
  `(ret − rf)` to `circular_block_bootstrap_sharpe`.
- `engine/backtest.py::_recent_turnover` was comparing the post-rebalance
  weights to an empty `Series`, producing a constant ≈ 1.0 turnover and
  inflating the cost drag in `portfolio_returns.parquet`.  A new
  `_prev_weights_for_cost` cache, snapshotted before the main loop
  overwrites `_prev_weights`, restores the correct rebalance-to-rebalance
  turnover and reconciles `(gross − net)` with `exposure_log.cost_drag_20bp`
  to within 1.3 × 10⁻⁵.
- `engine/portfolio.py::_iterative_cap` replaces the previous
  clip-then-renormalise step in `score_weighted_leg`, MinVar, and HRP.
  The old sequence pushed previously-capped weights back above the 5 %
  per-stock limit; the iterative version redistributes excess mass to
  uncapped names and converges within a few passes.
- `engine/backtest.py` now writes an empirical CAPM β to
  `exposure_log.portfolio_beta` (regression of daily portfolio returns
  against ^GSPC over a 252-day window) instead of a literal `0.0`.
- `engine/factors.py::compute_quality` was falling through to broken
  fallbacks (`1 / rank(|EPS|)` for stability, `eq / (|debt| + 0.01·|eq|)`
  for inverse D/E) because the original `earnings_stability` and
  `debt_to_equity_inv` columns are single-snapshot in CW1.  Now uses the
  400+-snapshot `_hist` variants (`roe_hist`, `debt_to_equity_hist`,
  `profit_margin_hist`) — the QMJ profitability proxy in
  Asness, Frazzini & Pedersen (2019) §III.A.

### Changed

- `factors.base_weights` reduced from `0.30 / 0.30 / 0.25 / 0.15` to
  `0.50 / 0.50 / 0.00 / 0.00` (momentum + value only).  Quality and
  sentiment retained in the pipeline for the diagnostic IC table but
  carry zero composite weight.  Decision rationale and IC numbers are
  in the report (§§1.2, 2.2.1, 4.2).
- `engine/bandit.py::build_arms` reduced from 12 four-factor arms to 8
  two-factor arms (mom/val splits around 0.50/0.50).  `bandit.n_arms`
  reduced from 12 to 8 in the config to match.
- `analytics/sensitivity.py::run_sensitivity_cpcv` now produces the full
  15 (γ, λ) × 66 CPCV-fold grid (990 rows).  The deflated Sharpe
  multiplicity penalty is computed at the grid-point level using the
  full-sample return distribution (per-fold subsamples are too short for
  the Bailey-López de Prado skew-and-kurtosis correction).

### Added

- `engine/backtest.py` populates `trade_ledger.parquet` with
  one immutable record per non-trivial weight change at each rebalance.
  Each row carries the action (open/close/adjust), old/new weight,
  notional USD, predicted impact (sqrt-law stub), proportional cost,
  rebalance UUID, seed, and data snapshot SHA-256.
- HRP variant routed through `optimise_leg(construction_override="hrp")`
  and surfaced as `portfolio_returns.hrp_net_20bp` for the §3.4.4
  robustness comparison.  Long-leg and short-leg realised monthly returns
  are populated under the canonical `DYNAMIC_GRID` book.
- Optional `pit_lag.fundamentals_days` and `pit_lag.ratios_days` config
  keys (default 0 → CW1/PLAN §7.3 behaviour) plumbed through
  `DataLoader.build_context` into the SQL cutoff of
  `load_fundamentals_pit` / `load_ratios_pit`.  Sensitivity at lag 30 and
  45 documented in the report (§3.3) — Dynamic Sharpe is essentially
  lag-invariant on the two-factor composite because momentum uses prices
  rather than fundamentals.
- `analytics/monte_carlo.py` — 10,000-path circular block bootstrap
  (Politis-Romano 1994, 6-month blocks) over the Dynamic net 20 bp return
  series.  Output: `output/monte_carlo_paths.parquet`.
- `analytics/regime_performance.py` — per-regime × per-strategy
  metric decomposition joined via `pd.merge_asof` against `regime_log`.
  Output: `output/regime_performance.parquet`.
- `engine/runner.py` modes `monte_carlo` and `regime_perf` for the
  post-backtest analytics that read existing parquet outputs without
  requiring the database.

### Tests

- 87 unit tests (was 72).  New: `test_cost_consistency.py` (3),
  `test_pit_lag.py` (6), `test_portfolio.py::_iterative_cap_*` (4),
  `test_bandit.py` 2-factor regression tests (2).

## [0.2.0] — 2026-04-17

Initial multi-factor backtest engine: dependency-injected event loop
across ten swappable components, monthly NYSE rebalancing via
`pandas_market_calendars`, parallel strategy variants (static / dynamic
grid / Thompson-sampling bandit), seven-Parquet data contract, full
audit trail with seed and data SHA-256.

### Engine

- `data_loader` (CW1 PostgreSQL, strict report_date PIT, liquidity filter)
- `factors` (4 factor scores, sequential Gram-Schmidt orthogonalisation)
- `zscore` (sector-neutral, within-GICS winsorisation, composite weighting)
- `portfolio` (MinVar with Ledoit-Wolf, denoised Ledoit-Wolf, turnover
  penalty, HRP)
- `costs` (proportional 20/30 bp per side)
- `dynamic_weights` (VIX percentile regime + cross-sectional dispersion)
- `bandit` (linear contextual Thompson sampling, conjugate Gaussian update)
- `risk_scaler` (HVaR → conditional vol target → drawdown-control)
- `attribution` (Fama-MacBeth, Kyle's-λ / Amihud capacity)
- `benchmark` (equal-weight universe, S&P 500, 50/50 cash-market blend)
- `backtest` (DI event-driven engine, full audit trail)
- `runner` / `Main.py` (CLI: `--mode {full, sensitivity, ablation, stress}`)

### Analytics

- `performance` (Sharpe / Sortino / IR / Calmar, drawdown duration,
  HVaR / ES, hit rate, block-bootstrap Sharpe CI, deflated Sharpe,
  probabilistic Sharpe, minimum backtest length)
- `validation` (engine-output integrity)
- `charts` (14 mandatory + 3 extension figures, locked colour palette)
- `sensitivity` (γ × λ grid with CPCV)
- `ablation` (5-variant factor ablation)
- `comparison` (static vs VIX-only vs dispersion-only vs combined)
- `stress` (3 crisis windows + Monte Carlo permutation test)
- `attribution_analysis` (FF5 + Mom regression with Newey-West HAC)

### Tests

72 unit tests across engine and analytics modules.  CW1↔CW2 integration
verified against the live `systematic_equity` schema (auto-skipped when
the database is unreachable).
