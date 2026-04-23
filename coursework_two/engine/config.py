"""Typed configuration loader for the CW2 backtest engine.

Security: schema identifier is validated against a strict regex to prevent
SQL-injection via interpolation (required because SQLAlchemy cannot bind
schema names as parameters).  All credential fields are env-overridable.


Loads ``config/backtest_config.yaml`` into a Pydantic settings object so every
parameter is validated, type-checked, and discoverable via autocomplete.  No
magic numbers anywhere else in the code-base — all knobs live here.

Usage
-----
>>> from engine.config import load_config
>>> cfg = load_config()
>>> cfg.factors.base_weights.momentum
0.3
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# =============================================================================
# Leaf config objects
# =============================================================================
class DatabaseConfig(BaseModel):
    host: str
    port: int
    name: str
    user: str
    password: str
    schema_: str = Field(alias="schema")
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("schema_", "name", "user")
    @classmethod
    def _validate_identifier(cls, v: str) -> str:
        """Reject SQL-injection-prone identifiers for fields we interpolate into SQL."""
        if not _IDENT_RE.match(v):
            raise ValueError(f"invalid SQL identifier: {v!r}")
        return v


class UniverseConfig(BaseModel):
    min_adv_usd: float
    bottom_pct_filter: float
    short_min_market_cap_usd: float
    short_min_adv_usd: float
    variant: Literal["default", "top500_adv", "us_only"] = "default"


class DatesConfig(BaseModel):
    in_sample_start: date
    in_sample_end: date
    oos_start: date
    oos_end: date
    extended_oos_end: date
    rebalance_frequency: Literal["monthly", "weekly", "quarterly"] = "monthly"
    trading_calendar: str = "NYSE"


class EstimationWindowsConfig(BaseModel):
    momentum_lookback_months: int
    momentum_skip_months: int
    covariance_days: int
    var_days: int
    vol_target_days: int
    vix_regime_days: int
    drawdown_lookback_months: int
    bayesian_signal_half_life_months: int


class PitLagConfig(BaseModel):
    """Optional filing-lag for fundamentals / ratios PIT queries.

    The brief (PLAN §7.3 rule 1) uses ``report_date ≤ rebalance_date`` as
    the PIT cutoff.  ``report_date`` is the fiscal period-end, NOT the
    public filing date — US large accelerated filers have a 40-day SEC
    10-Q deadline, so setting a positive lag gives a conservative PIT
    proxy for sensitivity analysis in Report §4 / §7 Limitations.

    Default 0 days on both fields preserves the brief-compliant default
    behaviour; any lag value ≥ 0 is routed through ``DataLoader``'s two
    PIT loaders via ``build_context``.
    """

    fundamentals_days: int = Field(default=0, ge=0)
    ratios_days: int = Field(default=0, ge=0)


class FactorBaseWeights(BaseModel):
    momentum: float
    value: float
    quality: float
    sentiment: float

    @field_validator("sentiment")
    @classmethod
    def _sum_to_one(cls, v, info):
        data = info.data
        total = data.get("momentum", 0) + data.get("value", 0) + data.get("quality", 0) + v
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Base factor weights must sum to 1.0, got {total}")
        return v


class FactorConfig(BaseModel):
    base_weights: FactorBaseWeights
    winsor_lower_pct: float
    winsor_upper_pct: float
    orthogonalise: bool
    orthogonalisation_order: list[str]
    min_sector_size: int


class RegimeTilts(BaseModel):
    low: FactorBaseWeights
    normal: FactorBaseWeights
    high: FactorBaseWeights

    @field_validator("low", "normal", "high")
    @classmethod
    def _allow_non_sum_one(cls, v):
        # Override parent validator; tilts need not sum to 1.
        return v


class DynamicWeightsConfig(BaseModel):
    vix_low_pct: float
    vix_high_pct: float
    regime_tilts: dict[str, dict[str, float]]
    gamma: float
    absolute_momentum_filter: bool


class PortfolioConfig(BaseModel):
    construction: Literal["minvar_lw", "minvar_denoised_lw", "minvar_turnover", "hrp", "score_weighted"]
    max_weight_per_stock: float
    min_weight_per_stock: float
    long_quartile: float
    short_quartile: float
    turnover_penalty_lambda: float
    denoise_enabled: bool


class RiskScalerConfig(BaseModel):
    hvar_confidence: float
    hvar_target_budget: float
    vol_target_enabled: bool
    vol_target_annual: float
    vol_target_clip_lower: float
    vol_target_clip_upper: float
    dd_control_enabled: bool
    dd_threshold_soft: float
    dd_threshold_hard: float
    dd_scalar_soft: float
    dd_scalar_hard: float
    dd_recover_threshold: float


class CostsConfig(BaseModel):
    cost_per_side_bp_headline: int
    cost_per_side_bp_sensitivity: int


class BanditConfig(BaseModel):
    enabled: bool
    n_arms: int
    context_dim: int
    prior_sigma2: float
    warmup_months: int
    reward_half_life_months: int
    random_seed: int


class BacktestConfig(BaseModel):
    initial_nav: float
    random_seed: int
    n_workers: int
    gamma_grid: list[float]
    lambda_magnitude_grid: list[float]
    cpcv_n_groups: int
    cpcv_test_groups: int
    cpcv_purge_months: int
    cpcv_embargo_months: int
    monte_carlo_n_paths: int
    monte_carlo_block_months: int


class OutputsConfig(BaseModel):
    output_dir: str
    charts_dir: str
    track_git_sha: bool
    track_data_hash: bool


class StressWindow(BaseModel):
    start: date
    end: date


class StressWindowsConfig(BaseModel):
    covid_2020: StressWindow
    rate_shock_2022: StressWindow
    q4_2025_reversal: StressWindow


class MLEnhancerConfig(BaseModel):
    enabled: bool
    model: str
    gate_min_oos_ic: float
    gate_min_sharpe_improvement: float


class LoggingConfig(BaseModel):
    level: str = "INFO"
    rich_tracebacks: bool = True


# =============================================================================
# Root config
# =============================================================================
class Config(BaseModel):
    database: DatabaseConfig
    universe: UniverseConfig
    dates: DatesConfig
    estimation_windows: EstimationWindowsConfig
    pit_lag: PitLagConfig = PitLagConfig()   # optional; defaults to 0/0
    factors: FactorConfig
    dynamic_weights: DynamicWeightsConfig
    portfolio: PortfolioConfig
    risk_scaler: RiskScalerConfig
    costs: CostsConfig
    bandit: BanditConfig
    backtest: BacktestConfig
    outputs: OutputsConfig
    stress_windows: StressWindowsConfig
    ml_enhancer: MLEnhancerConfig
    logging: LoggingConfig

    model_config = ConfigDict(frozen=False)

    # ------- Reproducibility helpers --------------------------------------
    def config_hash(self) -> str:
        payload = yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    @staticmethod
    def git_sha() -> str | None:
        try:
            import subprocess

            out = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=_repo_root(), stderr=subprocess.DEVNULL
            )
            return out.decode().strip()
        except Exception:
            return None


_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "backtest_config.yaml"


def _repo_root() -> Path:
    return Path(__file__).parent.parent


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate the YAML config file.

    Parameters
    ----------
    path : str | Path | None
        Optional override; defaults to ``config/backtest_config.yaml``.

    Returns
    -------
    Config
        Fully-validated Pydantic config object.
    """
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"Config not found at {p}")
    with p.open("r") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    # Environment overrides — enables CI + credential rotation without code changes
    # (security audit finding #1/#2: no credentials hardcoded into shared state)
    _env_map = {
        "POSTGRES_HOST": "host",
        "POSTGRES_PORT": "port",
        "POSTGRES_USER": "user",
        "POSTGRES_PASSWORD": "password",
        "POSTGRES_DATABASE": "name",
        "POSTGRES_SCHEMA": "schema",
    }
    for env_key, cfg_key in _env_map.items():
        if env_key in os.environ:
            val = os.environ[env_key]
            if cfg_key == "port":
                val = int(val)
            raw["database"][cfg_key] = val
    return Config(**raw)


__all__ = [
    "BacktestConfig",
    "BanditConfig",
    "Config",
    "CostsConfig",
    "DatabaseConfig",
    "DatesConfig",
    "DynamicWeightsConfig",
    "EstimationWindowsConfig",
    "FactorConfig",
    "LoggingConfig",
    "MLEnhancerConfig",
    "OutputsConfig",
    "PortfolioConfig",
    "RiskScalerConfig",
    "StressWindowsConfig",
    "UniverseConfig",
    "load_config",
]
