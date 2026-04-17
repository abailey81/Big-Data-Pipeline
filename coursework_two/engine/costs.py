"""Transaction-cost model — spec-mandated proportional costs (PLAN §7.10).

Two scenarios per CW2 Task-Allocation-Guide and Viz Reference:
    • **20 bp per side** — headline (Korajczyk & Sadka 2004 liquid-large-cap)
    • **30 bp per side** — sensitivity

Turnover formula (Viz Reference §1.4):
    turnover_t = (1/2) Σ_i |w_new_i − w_old_i|      (one-way)
    net return = gross return − turnover × cost_bps × 2   (round-trip)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.config import Config


@dataclass
class CostModel:
    cfg: Config

    @staticmethod
    def one_way_turnover(w_new: pd.Series, w_old: pd.Series | None) -> float:
        """One-way turnover = 0.5 · Σ|Δw|.

        If no prior weights (first rebalance), turnover equals 0.5 · Σ|w_new|.
        """
        if w_old is None or len(w_old) == 0:
            return 0.5 * float(w_new.abs().sum())
        aligned = w_new.reindex(w_new.index.union(w_old.index), fill_value=0.0)
        old = w_old.reindex(w_new.index.union(w_old.index), fill_value=0.0)
        return 0.5 * float((aligned - old).abs().sum())

    def cost_drag(self, one_way_to: float, cost_per_side_bp: float) -> float:
        """Proportional cost drag per rebalance on dollar notional basis.

        Net return = gross return − turnover × cost_bp × 2 (two sides).
        """
        return (one_way_to * 2.0 * cost_per_side_bp) / 10_000.0

    def headline_drag(self, one_way_to: float) -> float:
        return self.cost_drag(one_way_to, self.cfg.costs.cost_per_side_bp_headline)

    def sensitivity_drag(self, one_way_to: float) -> float:
        return self.cost_drag(one_way_to, self.cfg.costs.cost_per_side_bp_sensitivity)


__all__ = ["CostModel"]
