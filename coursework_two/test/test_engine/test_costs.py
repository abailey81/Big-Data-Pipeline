"""Cost-model tests."""

import pandas as pd
from engine.costs import CostModel


def test_first_rebalance_full_turnover(base_config):
    cm = CostModel(base_config)
    w_new = pd.Series({"A": 0.5, "B": -0.5})
    # No prior weights
    to = cm.one_way_turnover(w_new, None)
    assert abs(to - 0.5) < 1e-9   # 0.5 × (0.5 + 0.5) = 0.5


def test_no_change_zero_turnover(base_config):
    cm = CostModel(base_config)
    w = pd.Series({"A": 0.3, "B": -0.3})
    assert cm.one_way_turnover(w, w) == 0.0


def test_swap_full_turnover(base_config):
    cm = CostModel(base_config)
    w_old = pd.Series({"A": 0.5, "B": -0.5})
    w_new = pd.Series({"A": -0.5, "B": 0.5})
    to = cm.one_way_turnover(w_new, w_old)
    # |-1| + |+1| = 2, ×0.5 = 1.0
    assert abs(to - 1.0) < 1e-9


def test_cost_drag_proportional(base_config):
    cm = CostModel(base_config)
    # Turnover 1.0 × 2 sides × 20bp = 40bp = 0.004
    assert abs(cm.cost_drag(1.0, 20) - 0.004) < 1e-9
    # 30bp scenario
    assert abs(cm.cost_drag(1.0, 30) - 0.006) < 1e-9
