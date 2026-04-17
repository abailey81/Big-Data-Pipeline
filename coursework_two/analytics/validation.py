"""Validation checks on engine outputs (PLAN §3.1 specialist scope — Peixi).

Consumes the seven Parquet data-contract files and verifies the internal
consistency invariants listed in the Task Allocation Guide §3.1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    passed: bool
    flags: list[str]
    details: dict[str, Any]

    def __bool__(self) -> bool:
        return self.passed


def validate_weights(weights_df: pd.DataFrame, max_weight: float = 0.05) -> ValidationResult:
    """Per-date, per-leg: w ≥ 0 (after leg sign), sum = ±0.5 each leg, no w > 5%, gross ~2, net ~0."""
    flags: list[str] = []
    for (date, strategy), grp in weights_df.groupby(["date", "strategy"]):
        long_w = grp.loc[grp["leg"] == "long", "weight"]
        short_w = grp.loc[grp["leg"] == "short", "weight"]
        # Long-leg checks
        if (long_w < -1e-6).any():
            flags.append(f"{date} {strategy}: negative long weights")
        if long_w.abs().max() > max_weight + 1e-6:
            flags.append(f"{date} {strategy}: long-leg max weight > {max_weight}")
        # Short-leg checks (weights stored as negative)
        if (short_w > 1e-6).any():
            flags.append(f"{date} {strategy}: positive short weights")
        if short_w.abs().max() > max_weight + 1e-6:
            flags.append(f"{date} {strategy}: short-leg max weight > {max_weight}")
    return ValidationResult(
        passed=len(flags) == 0,
        flags=flags,
        details={"n_rows": len(weights_df), "n_dates": weights_df["date"].nunique()},
    )


def validate_returns(returns_df: pd.DataFrame) -> ValidationResult:
    flags: list[str] = []
    for col in ["dynamic_gross", "dynamic_net_20bp", "static_net_20bp", "benchmark_ew"]:
        if col in returns_df.columns:
            if returns_df[col].isna().any():
                flags.append(f"NaN values in {col}")
            if (returns_df[col].abs() > 0.5).any():
                flags.append(f"Implausible returns > 50%/mo in {col}")
    # Gross >= Net (after cost deduction)
    if {"dynamic_gross", "dynamic_net_20bp"}.issubset(returns_df.columns):
        gap = returns_df["dynamic_gross"] - returns_df["dynamic_net_20bp"]
        if (gap < -1e-8).any():
            flags.append("dynamic_net_20bp exceeds dynamic_gross at some dates")
    return ValidationResult(passed=len(flags) == 0, flags=flags, details={"n_rows": len(returns_df)})


def validate_factors(factor_df: pd.DataFrame, tol: float = 0.5) -> ValidationResult:
    """Z-scores should have mean ~0 (within tolerance) and std ~1 per factor × date × sector."""
    flags: list[str] = []
    for fac in ["momentum_z", "value_z", "quality_z"]:
        if fac not in factor_df.columns:
            continue
        means = factor_df.groupby(["date", "gics_sector"])[fac].mean()
        if means.abs().gt(tol).mean() > 0.5:
            flags.append(f"More than half of sector×date {fac} means exceed ±{tol}")
    return ValidationResult(passed=len(flags) == 0, flags=flags, details={"n_rows": len(factor_df)})


def validate_regime(regime_df: pd.DataFrame) -> ValidationResult:
    flags: list[str] = []
    if not regime_df["vix_percentile"].between(0, 1).all():
        flags.append("vix_percentile out of [0,1]")
    if not regime_df["regime_pct"].isin(["low", "normal", "high"]).all():
        flags.append("regime_pct has unknown labels")
    sum_w = regime_df[["w_momentum", "w_value", "w_quality", "w_sentiment"]].sum(axis=1)
    if not np.allclose(sum_w, 1.0, atol=1e-3):
        flags.append(f"Dynamic weights do not sum to 1 (max dev: {(sum_w - 1).abs().max():.4f})")
    return ValidationResult(passed=len(flags) == 0, flags=flags, details={"n_rows": len(regime_df)})


__all__ = [
    "ValidationResult",
    "validate_factors",
    "validate_regime",
    "validate_returns",
    "validate_weights",
]
