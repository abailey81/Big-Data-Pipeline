"""Factor-ablation analysis (PLAN §5.13 / Viz Ref chart 13).

Five backtest variants:
    1. full_4factor      — baseline dynamic strategy
    2. no_momentum       — momentum weight = 0, others renormalised
    3. no_value          — value weight = 0
    4. no_quality        — quality weight = 0
    5. no_sentiment      — sentiment weight = 0

Output: ``ablation_results.parquet``.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from analytics.performance import (
    information_ratio,
    max_drawdown,
    sharpe_ratio,
)
from engine.backtest import BacktestEngine
from engine.config import Config, load_config
from engine.costs import CostModel
from engine.data_loader import DataLoader
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.types import Strategy
from engine.zscore import ZScoreEngine

logger = logging.getLogger(__name__)


ABLATION_VARIANTS = {
    "full_4factor": {"momentum": 0.30, "value": 0.30, "quality": 0.25, "sentiment": 0.15},
    "no_momentum":  {"momentum": 0.00, "value": 0.44, "quality": 0.35, "sentiment": 0.21},
    "no_value":     {"momentum": 0.44, "value": 0.00, "quality": 0.35, "sentiment": 0.21},
    "no_quality":   {"momentum": 0.40, "value": 0.40, "quality": 0.00, "sentiment": 0.20},
    "no_sentiment": {"momentum": 0.35, "value": 0.35, "quality": 0.30, "sentiment": 0.00},
    # Added 2026-04-22 in response to Lucian's proposal: test whether the
    # factor composite works better with the two-factor (momentum + value)
    # subset and the three-factor (momentum + value + quality) subset.
    # Sentiment IC = 0.000 for all 32 months in the current CW1 data, so
    # dropping it is a no-op at signal level — this variant makes that
    # explicit.  The "mom_val_only" variant is the 2-factor baseline
    # Lucian proposed.
    "no_sentiment_3factor": {"momentum": 0.35, "value": 0.35, "quality": 0.30, "sentiment": 0.00},
    "mom_val_only": {"momentum": 0.50, "value": 0.50, "quality": 0.00, "sentiment": 0.00},
    "mom_val_qual": {"momentum": 0.40, "value": 0.40, "quality": 0.20, "sentiment": 0.00},
}


def run_ablation(
    cfg: Config,
    start: date,
    end: date,
    out_dir: str | Path = "output",
) -> pd.DataFrame:
    """Run 5 ablation variants; return a one-row-per-variant DataFrame."""
    rows: list[dict] = []
    for variant, weights in ABLATION_VARIANTS.items():
        cfg_local = load_config()
        cfg_local.factors.base_weights.momentum = weights["momentum"]
        cfg_local.factors.base_weights.value = weights["value"]
        cfg_local.factors.base_weights.quality = weights["quality"]
        cfg_local.factors.base_weights.sentiment = weights["sentiment"]

        dl = DataLoader(cfg_local)
        fe = FactorEngine(cfg_local)
        ze = ZScoreEngine(cfg_local)
        pe = PortfolioEngine(cfg_local)
        cm = CostModel(cfg_local)
        engine = BacktestEngine(
            cfg=cfg_local, data_loader=dl, factor_engine=fe, zscore_engine=ze,
            portfolio_engine=pe, cost_model=cm,
        )
        try:
            result = engine.run(start=start, end=end, strategies_to_run=(Strategy.STATIC,))
            returns = result.returns["static_net_20bp"].dropna()
            bench = result.returns.get("benchmark_ew", pd.Series(dtype=float)).dropna()
            rows.append({
                "variant": variant,
                "sharpe_net": sharpe_ratio(returns, 0.0),
                "max_dd": max_drawdown(returns),
                "info_ratio": information_ratio(returns, bench),
                # alpha_ff5 / alpha_tstat are populated by the dedicated FF5
                # attribution path in analysis/run_attribution_ls.py against
                # portfolio_returns.parquet; left at zero here so the ablation
                # parquet schema stays stable across runs.
                "alpha_ff5": 0.0,
                "alpha_tstat": 0.0,
                "turnover": 0.0,
            })
            logger.info("Ablation %s: Sharpe=%.3f, MaxDD=%.3f", variant, rows[-1]["sharpe_net"], rows[-1]["max_dd"])
        except Exception as exc:
            logger.error("Ablation %s failed: %s", variant, exc)
    df = pd.DataFrame(rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df.to_parquet(Path(out_dir) / "ablation_results.parquet", index=False)
    return df


__all__ = ["run_ablation", "ABLATION_VARIANTS"]
