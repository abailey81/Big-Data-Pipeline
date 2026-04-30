"""Engine / analytics data contract (Pydantic models and TypedDicts).

This module locks the schemas for every Parquet file produced by the engine
and consumed by the analytics layer.  Agreed by Tamer + Lucian in Week 1 per
PLAN.md §3.2.  No other engine code should define output schemas.

The contract:
    portfolio_returns.parquet    : PortfolioReturnsRow
    portfolio_weights.parquet    : PortfolioWeightsRow
    factor_scores.parquet        : FactorScoresRow
    factor_ic.parquet            : FactorICRow
    factor_premia.parquet        : FactorPremiaRow           (§5.9 Fama-MacBeth)
    regime_log.parquet           : RegimeLogRow
    exposure_log.parquet         : ExposureLogRow
    sensitivity_grid.parquet     : SensitivityGridRow        (§5.5 CPCV)
    ablation_results.parquet     : AblationRow               (§5.13 ablation)
    bandit_log.parquet           : BanditLogRow              (§5.4 Thompson)
    monte_carlo_paths.parquet    : MonteCarloRow             (§7.5)
    trade_ledger.parquet         : TradeLedgerRow            (§7.9)

References
----------
Agrawal & Goyal (2013); Russo & Van Roy (2018); Bailey & López de Prado (2014).
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Enumerations
# =============================================================================
class Regime(str, Enum):
    """VIX-based volatility regime classification (§3.5 of CW1 report)."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Leg(str, Enum):
    LONG = "long"
    SHORT = "short"


class Strategy(str, Enum):
    """Weight-selection variant identity."""

    STATIC = "static"
    DYNAMIC_GRID = "dynamic_grid"
    DYNAMIC_BANDIT = "dynamic_bandit"
    HRP = "hrp"


class FactorName(str, Enum):
    MOMENTUM = "momentum"
    VALUE = "value"
    QUALITY = "quality"
    SENTIMENT = "sentiment"


# =============================================================================
# Contract rows (one per output Parquet)
# =============================================================================
class _BaseRow(BaseModel):
    """Frozen-style row model — forbid extra fields so contracts stay tight."""

    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


class PortfolioReturnsRow(_BaseRow):
    """One row per rebalance date with net/gross return per variant.

    Exactly the four columns required by the Viz & Metrics Reference §1:
    Dynamic Gross · Dynamic Net 20bp · Static Net 20bp · Benchmark EW.
    Plus 30bp sensitivity and HRP robustness.
    """

    date: date
    dynamic_gross: float
    dynamic_net_20bp: float
    dynamic_net_30bp: float
    static_net_20bp: float
    static_net_30bp: float
    hrp_net_20bp: Optional[float] = None
    bandit_net_20bp: Optional[float] = None
    benchmark_ew: float                               # §7.13 headline comparator
    benchmark_spx: Optional[float] = None             # S&P 500 market reference
    benchmark_cash_market_50_50: Optional[float] = None   # 50/50 cash+mkt blend
    long_leg: float
    short_leg: float
    rf_rate: float


class PortfolioWeightsRow(_BaseRow):
    date: date
    symbol: str
    weight: float
    leg: Leg
    strategy: Strategy


class FactorScoresRow(_BaseRow):
    """Per-stock, per-date sector-neutral z-scores (raw + orthogonalised)."""

    date: date
    symbol: str
    gics_sector: str
    # Raw z-scores
    momentum_z: float
    value_z: float
    quality_z: float
    sentiment_z: float
    # Orthogonalised z-scores (§5.14)
    momentum_z_ortho: Optional[float] = None
    value_z_ortho: Optional[float] = None
    quality_z_ortho: Optional[float] = None
    sentiment_z_ortho: Optional[float] = None
    composite_z: float


class FactorICRow(_BaseRow):
    date: date
    factor: FactorName
    ic_spearman: float
    ic_pearson: float
    forward_return: float


class FactorPremiaRow(_BaseRow):
    """§5.9 Fama-MacBeth per-factor monthly premium with t-stat."""

    date: date
    factor: FactorName
    fama_macbeth_beta: float
    t_stat: float
    r_squared: float
    n_stocks: int


class RegimeLogRow(_BaseRow):
    """§7.6 regime + dispersion + dynamic-weight log per date."""

    date: date
    vix_level: float
    vix_percentile: float
    regime_pct: Regime
    regime_hmm: Optional[Regime] = None
    hmm_prob_high: Optional[float] = None
    dispersion_momentum: float
    dispersion_value: float
    dispersion_quality: float
    dispersion_sentiment: float
    w_momentum: float
    w_value: float
    w_quality: float
    w_sentiment: float


class ExposureLogRow(_BaseRow):
    date: date
    gross_exposure: float
    net_exposure: float
    portfolio_beta: float
    var_99: float
    es_99: float
    position_scale: float
    vol_target_scalar: float
    dd_control_scalar: float
    drawdown_12m: float
    turnover_1way: float
    cost_drag_20bp: float
    cost_drag_30bp: float
    long_alpha: float
    short_alpha: float
    hhi_concentration: float
    n_stocks_long: int
    n_stocks_short: int
    n_stocks_filtered_liquidity: int
    n_stocks_filtered_htb: int


class SensitivityGridRow(_BaseRow):
    """§5.5 CPCV fold results — one row per (γ, λ, cv_fold)."""

    gamma: float
    lambda_magnitude: float
    cv_fold: int
    sharpe_net: float
    sharpe_deflated: float
    max_dd: float
    info_ratio: float
    turnover: float


class AblationRow(_BaseRow):
    variant: str                      # e.g. "full_4factor", "no_momentum"
    sharpe_net: float
    max_dd: float
    info_ratio: float
    alpha_ff5: float
    alpha_tstat: float
    turnover: float


class BanditLogRow(_BaseRow):
    """§5.4 Thompson Sampling per-date posteriors + selected arm."""

    date: date
    arm_selected: int
    realised_reward: float
    arm_posterior_mean_json: str          # JSON-packed K-vector
    arm_posterior_std_json: str
    context_vector_json: str


class MonteCarloRow(_BaseRow):
    path_id: int
    date: date
    nav: float


class TradeLedgerRow(_BaseRow):
    """§7.9 Immutable per-trade record."""

    date: date
    symbol: str
    side: Leg
    action: Literal["open", "close", "adjust"]
    old_weight: float
    new_weight: float
    notional_usd: float
    predicted_impact_bp: float
    proportional_cost_bp: float
    leg_id: str
    rebalance_id: str
    seed: int
    data_snapshot_sha256: str


# =============================================================================
# In-memory domain objects used inside the engine (not persisted)
# =============================================================================
class UniverseSnapshot(_BaseRow):
    """Point-in-time universe context at a rebalance date."""

    date: date
    symbols: list[str]
    gics_map: dict[str, str]           # symbol -> GICS sector
    currency_map: dict[str, str]       # symbol -> ISO currency


class FactorPayload(_BaseRow):
    """Raw factor scores pre-z-score."""

    date: date
    momentum: dict[str, float] = Field(default_factory=dict)
    value: dict[str, float] = Field(default_factory=dict)
    quality: dict[str, float] = Field(default_factory=dict)
    sentiment: dict[str, float] = Field(default_factory=dict)


# =============================================================================
# Protocol declarations (Strategy pattern — swappable components, §7.1)
# =============================================================================
__all__ = [
    "AblationRow",
    "BanditLogRow",
    "ExposureLogRow",
    "FactorICRow",
    "FactorName",
    "FactorPayload",
    "FactorPremiaRow",
    "FactorScoresRow",
    "Leg",
    "MonteCarloRow",
    "PortfolioReturnsRow",
    "PortfolioWeightsRow",
    "Regime",
    "RegimeLogRow",
    "SensitivityGridRow",
    "Strategy",
    "TradeLedgerRow",
    "UniverseSnapshot",
]
