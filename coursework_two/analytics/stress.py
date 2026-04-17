"""Stress testing — COVID, 2022 rate shock, Q4 2025 reversal (PLAN §10.4).

Plus Monte Carlo permutation test for dynamic-vs-static Sharpe gap (§5.13).

Output: ``stress_results.parquet``.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.performance import max_drawdown, sharpe_ratio
from engine.backtest import BacktestEngine
from engine.config import Config, load_config
from engine.costs import CostModel
from engine.data_loader import DataLoader
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.types import Strategy
from engine.zscore import ZScoreEngine

logger = logging.getLogger(__name__)


def _run_window(cfg: Config, start: date, end: date, label: str) -> dict:
    engine = BacktestEngine(
        cfg=cfg,
        data_loader=DataLoader(cfg),
        factor_engine=FactorEngine(cfg),
        zscore_engine=ZScoreEngine(cfg),
        portfolio_engine=PortfolioEngine(cfg),
        cost_model=CostModel(cfg),
    )
    try:
        result = engine.run(start=start, end=end, strategies_to_run=(Strategy.STATIC, Strategy.DYNAMIC_GRID))
        static_r = result.returns["static_net_20bp"].dropna()
        dyn_r = result.returns["dynamic_net_20bp"].dropna()
        return {
            "window": label,
            "start": start,
            "end": end,
            "static_sharpe": sharpe_ratio(static_r, 0.0),
            "dynamic_sharpe": sharpe_ratio(dyn_r, 0.0),
            "static_maxdd": max_drawdown(static_r),
            "dynamic_maxdd": max_drawdown(dyn_r),
            "n_months": len(static_r),
        }
    except Exception as exc:
        logger.error("Stress %s failed: %s", label, exc)
        return {"window": label, "start": start, "end": end, "error": str(exc)}


def run_stress(cfg: Config, out_dir: str | Path = "output") -> pd.DataFrame:
    windows = cfg.stress_windows
    rows = [
        _run_window(cfg, windows.covid_2020.start, windows.covid_2020.end, "covid_2020"),
        _run_window(cfg, windows.rate_shock_2022.start, windows.rate_shock_2022.end, "rate_shock_2022"),
        _run_window(cfg, windows.q4_2025_reversal.start, windows.q4_2025_reversal.end, "q4_2025_reversal"),
    ]
    df = pd.DataFrame(rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df.to_parquet(Path(out_dir) / "stress_results.parquet", index=False)
    return df


# =============================================================================
# Monte Carlo permutation test (§5.13)
# =============================================================================
def permutation_test_dynamic_vs_static(
    returns_df: pd.DataFrame,
    n_permutations: int = 10_000,
    seed: int = 42,
) -> dict:
    """Null: dynamic and static draw from same distribution.

    Under H0, permute the dynamic/static labels across months, recompute
    the Sharpe difference.  Return empirical p-value.
    """
    rng = np.random.default_rng(seed)
    d = returns_df["dynamic_net_20bp"].dropna().values
    s = returns_df["static_net_20bp"].dropna().values
    T = min(len(d), len(s))
    d, s = d[:T], s[:T]
    observed = sharpe_ratio(pd.Series(d), 0.0) - sharpe_ratio(pd.Series(s), 0.0)
    pooled = np.concatenate([d, s])
    diffs = np.empty(n_permutations)
    for i in range(n_permutations):
        rng.shuffle(pooled)
        d_perm = pooled[:T]
        s_perm = pooled[T:T + T]
        diffs[i] = sharpe_ratio(pd.Series(d_perm), 0.0) - sharpe_ratio(pd.Series(s_perm), 0.0)
    p_value = float((np.abs(diffs) >= abs(observed)).mean())
    return {
        "observed_sharpe_gap": float(observed),
        "p_value": p_value,
        "null_mean": float(diffs.mean()),
        "null_std": float(diffs.std()),
        "n_permutations": n_permutations,
    }


__all__ = ["permutation_test_dynamic_vs_static", "run_stress"]
