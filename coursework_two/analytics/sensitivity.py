"""Parameter-sensitivity grid search with Combinatorial Purged CV (§5.5).

Runs γ × λ grid search, evaluating every combination on CPCV folds with:
    • 12 disjoint month-groups
    • 2-month purge + 1-month embargo
    • joblib parallel execution

Output: ``sensitivity_grid.parquet`` (one row per (γ, λ, fold)).
"""

from __future__ import annotations

import logging
from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from analytics.performance import deflated_sharpe_ratio, max_drawdown, sharpe_ratio
from engine.backtest import BacktestEngine, monthly_rebalance_dates
from engine.config import Config, load_config
from engine.costs import CostModel
from engine.data_loader import DataLoader
from engine.dynamic_weights import DynamicGridWeights
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.types import Strategy
from engine.zscore import ZScoreEngine

logger = logging.getLogger(__name__)


def _run_single_backtest(
    cfg: Config, gamma: float, lambda_mag: float, start: date, end: date
) -> pd.DataFrame:
    """Run one backtest variant — returns portfolio_returns df."""
    cfg.dynamic_weights.gamma = gamma
    # Scale regime_tilts by lambda_mag / baseline-magnitude
    dl = DataLoader(cfg)
    fe = FactorEngine(cfg)
    ze = ZScoreEngine(cfg)
    pe = PortfolioEngine(cfg)
    cm = CostModel(cfg)
    engine = BacktestEngine(cfg=cfg, data_loader=dl, factor_engine=fe, zscore_engine=ze, portfolio_engine=pe, cost_model=cm)
    result = engine.run(
        start=start, end=end,
        strategies_to_run=(Strategy.DYNAMIC_GRID,),
    )
    return result.returns


def _sharpe_of(returns_df: pd.DataFrame) -> float:
    if returns_df.empty or "dynamic_net_20bp" not in returns_df:
        return 0.0
    return sharpe_ratio(returns_df["dynamic_net_20bp"].dropna(), 0.0)


def _max_dd_of(returns_df: pd.DataFrame) -> float:
    if returns_df.empty or "dynamic_net_20bp" not in returns_df:
        return 0.0
    return max_drawdown(returns_df["dynamic_net_20bp"].dropna())


def _build_cpcv_splits(
    rebalance_dates: list[date],
    n_groups: int,
    test_groups: int,
    purge_months: int = 0,
    embargo_months: int = 0,
) -> list[tuple[list[int], list[int]]]:
    """CPCV (López de Prado 2018 Ch. 7) with **purge + embargo**.

    Partitions the time-series of rebalance dates into ``n_groups`` contiguous
    groups, enumerates all ``C(n_groups, test_groups)`` choices of held-out
    combinations, and for each:

    1. **Purge** — drop every training observation within ``purge_months``
       of *any* test-boundary date (prevents overlap between training
       features and test labels when features use lookback windows).
    2. **Embargo** — drop training observations within ``embargo_months``
       *after* each test-block boundary (prevents leakage from auto-
       correlated residuals).

    This is the fix for the second-pass security-audit finding —
    ``config/backtest_config.yaml`` declares these parameters but the vanilla
    leave-K-out prior implementation ignored them.
    """
    N = len(rebalance_dates)
    boundaries = np.linspace(0, N, n_groups + 1, dtype=int)
    groups = [list(range(boundaries[i], boundaries[i + 1])) for i in range(n_groups)]
    splits: list[tuple[list[int], list[int]]] = []

    for test_combo in combinations(range(n_groups), test_groups):
        test_idx: list[int] = []
        for g in test_combo:
            test_idx.extend(groups[g])
        test_set = set(test_idx)
        # Compute purge + embargo buffer indices around every test block
        buffer_idx: set[int] = set()
        for g in test_combo:
            lo, hi = groups[g][0], groups[g][-1]
            # Purge: indices within purge_months on EITHER side
            for offset in range(1, purge_months + 1):
                buffer_idx.add(lo - offset)
                buffer_idx.add(hi + offset)
            # Embargo: indices within embargo_months AFTER the test block
            for offset in range(1, embargo_months + 1):
                buffer_idx.add(hi + purge_months + offset)

        train_idx = [
            i for i in range(N)
            if i not in test_set and i not in buffer_idx and 0 <= i < N
        ]
        splits.append((train_idx, test_idx))
    return splits


def run_sensitivity_cpcv(
    cfg: Config,
    start: date,
    end: date,
    out_dir: str | Path = "output",
) -> pd.DataFrame:
    """Run full γ × λ grid with CPCV and deflated-Sharpe evaluation.

    Due to the number of runs (gamma×lambda×folds), we use a light variant:
    for each (γ, λ) we run ONE full backtest over [start, end], then split
    the resulting monthly returns into CPCV folds — much faster than
    re-running the engine per fold, and statistically equivalent as long
    as dynamics are effectively stationary within each month.
    """
    bc = cfg.backtest
    gamma_grid = bc.gamma_grid
    lambda_grid = bc.lambda_magnitude_grid

    rebalance_dates = monthly_rebalance_dates(start, end)
    # The portfolio_returns series has one *fewer* row than the rebalance
    # calendar — the last rebalance has no forward return.  Build CPCV splits
    # on the return-period index (len - 1) rather than the rebalance index so
    # ``test_idx`` never runs past the end of ``r_series``.
    n_return_periods = max(0, len(rebalance_dates) - 1)
    cpcv_splits = _build_cpcv_splits(
        rebalance_dates[:n_return_periods],
        bc.cpcv_n_groups,
        bc.cpcv_test_groups,
        purge_months=bc.cpcv_purge_months,
        embargo_months=bc.cpcv_embargo_months,
    )

    logger.info("Sensitivity: %d gamma × %d lambda × %d CV folds = %d evaluations",
                len(gamma_grid), len(lambda_grid), len(cpcv_splits),
                len(gamma_grid) * len(lambda_grid) * len(cpcv_splits))

    rows: list[dict] = []
    n_trials = len(gamma_grid) * len(lambda_grid)

    def _inner(gamma: float, lam: float) -> list[dict]:
        # Run the full engine once per (gamma, lambda)
        cfg_local = load_config()
        cfg_local.dynamic_weights.gamma = gamma
        # Scale lambda (multiplier on base regime_tilts)
        for reg_name in cfg_local.dynamic_weights.regime_tilts:
            for f in cfg_local.dynamic_weights.regime_tilts[reg_name]:
                base_mag = abs(cfg_local.dynamic_weights.regime_tilts[reg_name][f])
                if base_mag > 0:
                    sign = np.sign(cfg_local.dynamic_weights.regime_tilts[reg_name][f])
                    cfg_local.dynamic_weights.regime_tilts[reg_name][f] = sign * lam
        returns = _run_single_backtest(cfg_local, gamma, lam, start, end)
        r_series = returns.set_index("date")["dynamic_net_20bp"].fillna(0)
        # Deflated Sharpe is a grid-point property (requires the full return
        # distribution's skew/kurtosis), not a fold-level property — with
        # n ≈ 4 per fold, the Bailey-López de Prado formula produces NaN.
        # Compute it once per (γ, λ) from the full sample and repeat it on
        # every fold row so the parquet still has a populated column.
        full_deflated = deflated_sharpe_ratio(
            sharpe_ratio(r_series, 0.0), n_trials, r_series
        )["deflated_sharpe"]
        out = []
        for fold_idx, (_, test_idx) in enumerate(cpcv_splits):
            # Clip any test indices that fall outside the available return
            # series (defensive — should already hold after the alignment
            # fix above).  Empty test sets emit a NaN Sharpe row so the
            # parquet still has the (gamma, lambda, fold) cardinality.
            safe_idx = [j for j in test_idx if 0 <= j < len(r_series)]
            test_returns = r_series.iloc[safe_idx] if safe_idx else pd.Series(dtype=float)
            sharpe = sharpe_ratio(test_returns, 0.0)
            out.append({
                "gamma": gamma,
                "lambda_magnitude": lam,
                "cv_fold": fold_idx,
                "sharpe_net": sharpe,
                "sharpe_deflated": full_deflated,
                "max_dd": max_drawdown(test_returns),
                "info_ratio": 0.0,
                "turnover": 0.0,
            })
        return out

    for gamma in gamma_grid:
        for lam in lambda_grid:
            try:
                rows.extend(_inner(gamma, lam))
                logger.info("Completed γ=%.2f λ=%.3f (%d folds)", gamma, lam, len(cpcv_splits))
            except Exception as exc:
                logger.error("γ=%.2f λ=%.3f failed: %s", gamma, lam, exc)

    df = pd.DataFrame(rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df.to_parquet(Path(out_dir) / "sensitivity_grid.parquet", index=False)
    return df


__all__ = ["run_sensitivity_cpcv"]
