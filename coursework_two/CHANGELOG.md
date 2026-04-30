# Changelog

All notable changes to the CW2 backtest engine.  Format follows
[Keep a Changelog](https://keepachangelog.com/), semantic versioning.

## [0.3.3] — 2026-04-30 (submission alignment)

### Changed

- `notebooks/CW2_Tearsheet.ipynb` — six narrative cells rewritten to align
  with every numeric claim in the submitted report (Tables 1, 5, 8, 9, 10,
  11 [§5.2 regime], 11 [§4.3.1 bootstrap], 12 PSR, 13 DSR, 15):
    - Cell 11 (Interpretation): Static raw +1.505 / excess +1.087 (+17.83 %
      ann., 11.39 % vol, max DD −7.86 %); Dynamic +1.404 / +0.997 (+16.92 %,
      11.67 %, −8.64 %); cumulative growth 1.517 / 1.549 / 1.342 vs EW.
    - Cell 15 (Bootstrap reading): four-variant 95 % CI table from Table 11;
      Politis-Romano 2,000 resamples, **block length 3 months** (was 6);
      PSR Dynamic 97.1 % / 88.8 % / 70.7 %; DSR threshold 1.771.
    - Cell 56 (Regime-conditional): Dynamic vs Static excess SR by regime
      (Low/Normal/High VIX) reproducing report Table 11 of §5.2.
    - Cell 59 (Permutation honest reading): observed gap +0.061, percentiles
      −1.903 / +1.874, p = 0.948 (10,000 permutations).
    - Cell 62 (How to read the table): four bullets restated against the
      report's authoritative numbers; block length corrected to 3 months.
    - Cell 63 (Conclusions): "What evidence supports / does not support"
      restated against Table 15 four-variant comparison and the report's
      §6 (Limitations) — short-borrow cost note (150 bp haircut compresses
      Dynamic excess Sharpe from +0.997 to ≈ +0.87).
- `notebooks/CW2_Tearsheet.ipynb` code cells: bootstrap parameters changed
  from `block_size=6, n_bootstrap=5000` to `block_size=3, n_bootstrap=2000`
  to match the report's bootstrap configuration.
- `engine/zscore.py`, `engine/dynamic_weights.py`, `analytics/comparison.py`:
  docstrings updated to reflect the implemented 50/50 mom + value composite
  rather than the original CW1 30/30/25/15 four-factor proposal.
- `docs/architecture_diagram.md` Mermaid sequence: WeightEngine box label
  "30/30/25/15" → "50/50 mom + value baseline".
- `docs/index.rst`, `docs/architecture.rst`, `docs/usage.rst`,
  `docs/installation.rst`, `docs/conf.py`: stale four-factor framing,
  artefact counts (9 → 17), version banner (0.2.0 → 0.3.2), and
  ablation-variant counts (5 → 8) corrected.

### Removed

- `scripts/build_notebook.py` — 1,319-line generator targeting
  `notebooks/tearsheet.ipynb` (a different filename) with comprehensively
  stale four-factor narrative and Sharpe 1.29 / 0.76 numbers from the
  earlier project era.  `notebooks/CW2_Tearsheet.ipynb` is the canonical
  hand-edited deliverable.

### Documentation

- `docs/_build/html/` Sphinx site committed (8.5 MB, 14 HTML pages over
  22 modules) so markers can browse the API reference without a Python
  setup.  Source RST files under `docs/`, build with
  `poetry run python -m sphinx -b html docs docs/_build/html`.
- `pyproject.toml` version 0.1.0 → 0.3.2.
- `scripts/validate_cw1_integration.py`: emoji checkmarks (✅/❌) replaced
  with plain `OK` / `MISSING` / `FAIL` tokens to match the published
  `reports/cw1_integration.md` style.

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
  (Politis-Romano 1994, 3-month blocks per report Table 11) over the
  Dynamic net 20 bp return series.  Output: `output/monte_carlo_paths.parquet`.
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
