"""Dependency-injected backtest engine (PLAN §7.1).

Ten swappable components compose the monthly-rebalancing loop:
    data_loader · factor_engine · zscore_engine · weight_engine ·
    portfolio_engine · risk_scaler · cost_model · executor · ledger ·
    metric_tracker

Each component is a plain dataclass with a well-defined interface, so every
variant (Denoised LW vs HRP, static vs dynamic vs bandit) is a one-line
swap — no forked code paths.

Output: the seven Parquet files of the §6 Data Contract.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd

try:
    import pandas_market_calendars as mcal
    _HAS_MCAL = True
except Exception:
    _HAS_MCAL = False

from engine.attribution import fama_macbeth_one_date
from engine.bandit import BanditWeights
from engine.benchmark import CashMarketBlend, EqualWeightBenchmark, SPXBenchmark
from engine.config import Config
from engine.costs import CostModel
from engine.data_loader import DataLoader, PITContext
from engine.dynamic_weights import DynamicGridWeights, StaticWeights
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.risk_scaler import CompositeRiskScaler
from engine.types import (
    BanditLogRow,
    ExposureLogRow,
    FactorICRow,
    FactorPremiaRow,
    FactorScoresRow,
    Leg,
    PortfolioReturnsRow,
    PortfolioWeightsRow,
    Regime,
    RegimeLogRow,
    Strategy,
    TradeLedgerRow,
)
from engine.zscore import ZScoreEngine

logger = logging.getLogger(__name__)


# =============================================================================
# Calendar (§7.2)
# =============================================================================
def monthly_rebalance_dates(start: date, end: date, calendar: str = "NYSE") -> list[date]:
    """Last trading day of each month between start and end."""
    if _HAS_MCAL:
        cal = mcal.get_calendar(calendar)
        sched = cal.schedule(start_date=start, end_date=end)
        days = pd.to_datetime(sched.index.date)
        out = []
        last_m = None
        last_d = None
        for d in days:
            ym = (d.year, d.month)
            if last_m and ym != last_m and last_d is not None:
                out.append(last_d.date())
            last_m = ym
            last_d = d
        if last_d is not None:
            out.append(last_d.date())
        return out
    # Fallback: pandas month-end
    rng = pd.date_range(start, end, freq="BME")
    return [d.date() for d in rng]


# =============================================================================
# BacktestResult — collects all Parquet-contract outputs
# =============================================================================
@dataclass
class BacktestResult:
    returns: pd.DataFrame
    weights: pd.DataFrame
    factor_scores: pd.DataFrame
    factor_ic: pd.DataFrame
    factor_premia: pd.DataFrame
    regime_log: pd.DataFrame
    exposure_log: pd.DataFrame
    bandit_log: pd.DataFrame
    trade_ledger: pd.DataFrame
    config_hash: str
    data_snapshot_sha256: str
    git_sha: str | None
    seed: int

    def save(self, out_dir: str | Path) -> None:
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        self.returns.to_parquet(p / "portfolio_returns.parquet", index=False)
        self.weights.to_parquet(p / "portfolio_weights.parquet", index=False)
        self.factor_scores.to_parquet(p / "factor_scores.parquet", index=False)
        self.factor_ic.to_parquet(p / "factor_ic.parquet", index=False)
        self.factor_premia.to_parquet(p / "factor_premia.parquet", index=False)
        self.regime_log.to_parquet(p / "regime_log.parquet", index=False)
        self.exposure_log.to_parquet(p / "exposure_log.parquet", index=False)
        self.bandit_log.to_parquet(p / "bandit_log.parquet", index=False)
        self.trade_ledger.to_parquet(p / "trade_ledger.parquet", index=False)
        meta = pd.DataFrame(
            [{"config_hash": self.config_hash,
              "data_snapshot_sha256": self.data_snapshot_sha256,
              "git_sha": self.git_sha or "",
              "seed": self.seed,
              "n_rebalances": len(self.returns)}]
        )
        meta.to_parquet(p / "backtest_metadata.parquet", index=False)


# =============================================================================
# Backtest engine
# =============================================================================
@dataclass
class BacktestEngine:
    """Main event-driven backtest loop with strict PIT discipline."""

    cfg: Config
    data_loader: DataLoader
    factor_engine: FactorEngine
    zscore_engine: ZScoreEngine
    portfolio_engine: PortfolioEngine
    cost_model: CostModel

    # Outputs
    _returns_rows: list[dict] = field(default_factory=list)
    _weights_rows: list[dict] = field(default_factory=list)
    _factor_rows: list[dict] = field(default_factory=list)
    _ic_rows: list[dict] = field(default_factory=list)
    _premia_rows: list[dict] = field(default_factory=list)
    _regime_rows: list[dict] = field(default_factory=list)
    _exposure_rows: list[dict] = field(default_factory=list)
    _bandit_rows: list[dict] = field(default_factory=list)
    _ledger_rows: list[dict] = field(default_factory=list)

    _prev_weights: dict[Strategy, pd.Series] = field(default_factory=dict)
    # Separate cache for cost-drag calculation in `_assemble_portfolio_returns_row`.
    # The `_prev_weights` dict is mutated to the CURRENT weights before the
    # assembler runs, so a turnover query against it would compare
    # w_current to w_current (= 0).  We snapshot the pre-update weights here
    # so `_recent_turnover` sees (new, old) not (new, empty).
    _prev_weights_for_cost: dict[Strategy, pd.Series] = field(default_factory=dict)
    _prev_longs: dict[Strategy, list[str]] = field(default_factory=dict)
    _prev_shorts: dict[Strategy, list[str]] = field(default_factory=dict)
    _nav: dict[Strategy, float] = field(default_factory=dict)
    _ic_rolling: list[dict[str, float]] = field(default_factory=list)   # 3mo IC window
    _prev_factor_z: Optional[pd.DataFrame] = None  # for Fama-MacBeth
    _data_snapshot_sha256: str = ""   # cached once at run() start for ledger rows
    # HRP side-run state (PLAN §5.3 robustness comparison — shares the
    # DYNAMIC_GRID factor weights but constructs the per-leg book via HRP
    # instead of score-weighting, so the report can show HRP vs the primary
    # book head-to-head.  Stored in portfolio_returns.hrp_net_20bp.)
    _hrp_prev_w: Optional[pd.Series] = None
    _hrp_monthly_returns: dict = field(default_factory=dict)
    # Long/short leg realised monthly returns (PLAN §6 data contract —
    # populated under DYNAMIC_GRID and merged into portfolio_returns.parquet
    # alongside hrp_net_20bp).
    _long_leg_returns: dict = field(default_factory=dict)
    _short_leg_returns: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def _initial_nav(self) -> float:
        return self.cfg.backtest.initial_nav

    # ------------------------------------------------------------------
    def _long_short_symbols(
        self,
        composite: pd.Series,
        prev_longs: Optional[list[str]] = None,
        prev_shorts: Optional[list[str]] = None,
        z_threshold: float = 0.0,
    ) -> tuple[list[str], list[str]]:
        """Quartile-based leg selection with **hysteresis band** to reduce turnover.

        Standard academic/industry practice (Grinold & Kahn 2000 Ch. 12): a
        currently-held long is retained if its composite rank remains in the
        top 30%; new longs require top 20%.  Same for shorts, inverted.  This
        reduces border-churn without altering the underlying quartile rule.
        """
        q_long = composite.quantile(1 - self.cfg.portfolio.long_quartile)
        q_short = composite.quantile(self.cfg.portfolio.short_quartile)
        # Core longs/shorts per spec + threshold filter (task §2 "threshold filters")
        core_longs = composite[(composite >= q_long) & (composite >= z_threshold)].index.tolist()
        core_shorts = composite[(composite <= q_short) & (composite <= -z_threshold)].index.tolist()
        # Hysteresis bands — widen retention zone by 5 percentile points
        hy_long = composite.quantile(1 - (self.cfg.portfolio.long_quartile + 0.05))
        hy_short = composite.quantile(self.cfg.portfolio.short_quartile + 0.05)
        longs = set(core_longs)
        shorts = set(core_shorts)
        if prev_longs is not None:
            longs |= {s for s in prev_longs if s in composite.index and composite[s] >= hy_long}
        if prev_shorts is not None:
            shorts |= {s for s in prev_shorts if s in composite.index and composite[s] <= hy_short}
        # Never allow overlap
        shorts -= longs
        return sorted(longs), sorted(shorts)

    # ------------------------------------------------------------------
    def _apply_momentum_filter(
        self, longs: list[str], momentum: pd.Series
    ) -> list[str]:
        """Antonacci (2016) absolute-momentum filter: drop negative-momentum longs."""
        if not self.cfg.dynamic_weights.absolute_momentum_filter:
            return longs
        return [s for s in longs if s in momentum.index and momentum[s] > 0]

    # ------------------------------------------------------------------
    def _simple_forward_return(
        self, ctx_next: PITContext, symbols: list[str], window_days: int = 21
    ) -> pd.Series:
        """Next rebalance's realised return for each symbol (approx 1M)."""
        px = ctx_next.prices
        if len(px) < window_days + 1:
            return pd.Series(dtype=float)
        recent = px.iloc[-window_days - 1:]
        r = recent.iloc[-1] / recent.iloc[0] - 1
        return r.reindex(symbols)

    # ------------------------------------------------------------------
    def run(
        self,
        start: date,
        end: date,
        strategies_to_run: tuple[Strategy, ...] = (
            Strategy.STATIC,
            Strategy.DYNAMIC_GRID,
            Strategy.DYNAMIC_BANDIT,
        ),
        use_hmm: bool = False,
        risk_scaler_factory=None,
    ) -> BacktestResult:
        """Run the backtest end-to-end from ``start`` to ``end`` (inclusive).

        Each strategy variant runs in lock-step on the *same* rebalance dates,
        sharing PIT context (expensive DB queries) but forking at weight
        selection.  Output: single BacktestResult covering all variants.
        """
        rebalance_dates = monthly_rebalance_dates(start, end, self.cfg.dates.trading_calendar)
        logger.info("Running backtest: %d rebalance dates %s → %s",
                    len(rebalance_dates), rebalance_dates[0], rebalance_dates[-1])
        # Cache the snapshot hash once so every TradeLedgerRow carries the same
        # reproducibility fingerprint without re-hashing on every trade.
        try:
            self._data_snapshot_sha256 = self.data_loader.data_snapshot_sha256()
        except Exception as exc:
            logger.warning("Could not fetch data_snapshot_sha256: %s", exc)
            self._data_snapshot_sha256 = "unknown"

        # Per-strategy state
        static_engine = StaticWeights(self.cfg)
        dynamic_engine = DynamicGridWeights(self.cfg)
        bandit_engine = BanditWeights(self.cfg)
        # Benchmark engines
        ew_bench = EqualWeightBenchmark(self.cfg, self.data_loader)
        spx_bench = SPXBenchmark(self.cfg, self.data_loader)
        blend_bench = CashMarketBlend(self.cfg, self.data_loader)
        self._ew_bench_weights_prev: Optional[pd.Series] = None

        risk_scalers: dict[Strategy, CompositeRiskScaler] = {}
        for s in strategies_to_run:
            risk_scalers[s] = (risk_scaler_factory() if risk_scaler_factory else CompositeRiskScaler(self.cfg))

        for s in strategies_to_run:
            self._nav[s] = self._initial_nav()
            self._prev_weights[s] = pd.Series(dtype=float)

        # Context cache
        prev_ctx: Optional[PITContext] = None

        for i, rb_date in enumerate(rebalance_dates):
            ctx = self.data_loader.build_context(
                rb_date, price_lookback_days=756, apply_liquidity_filter=True
            )

            # 1) Compute raw factors + z-scores
            payload = self.factor_engine.compute_all(ctx)
            raw = self.factor_engine.to_long_df(payload, ctx.universe.symbols)
            z_raw = self.zscore_engine.zscore_cross_section(raw, ctx.universe.gics_map)
            z_ortho = self.zscore_engine.apply_orthogonalisation(z_raw, ctx.universe.gics_map)

            # 2) Compute forward returns (using prev context → this context)
            if prev_ctx is not None:
                fwd_ret = self._simple_forward_return(ctx, list(z_ortho.index))
                # Log factor IC at prior date (so IC_{t-1} → r_t alignment is correct)
                ic_df = self.zscore_engine.factor_ic(
                    self._prev_factor_z if self._prev_factor_z is not None else z_ortho, fwd_ret
                )
                for _, row in ic_df.iterrows():
                    self._ic_rows.append({
                        "date": prev_ctx.rebalance_date,
                        "factor": row["factor"],
                        "ic_spearman": float(row["ic_spearman"]) if not pd.isna(row["ic_spearman"]) else 0.0,
                        "ic_pearson": float(row["ic_pearson"]) if not pd.isna(row["ic_pearson"]) else 0.0,
                        "forward_return": float(fwd_ret.mean()) if len(fwd_ret) > 0 else 0.0,
                    })

                # Fama-MacBeth one-step
                if self._prev_factor_z is not None:
                    fmb = fama_macbeth_one_date(self._prev_factor_z, fwd_ret)
                    if fmb:
                        for f in ("momentum", "value", "quality", "sentiment"):
                            if f in fmb:
                                self._premia_rows.append({
                                    "date": prev_ctx.rebalance_date,
                                    "factor": f,
                                    "fama_macbeth_beta": fmb[f],
                                    "t_stat": fmb.get(f"t_{f}", float("nan")),
                                    "r_squared": fmb.get("r_squared", float("nan")),
                                    "n_stocks": fmb.get("n", 0),
                                })

            # 3) Select weights per strategy variant
            # Use trailing-252d VIX z for bandit context
            vix_z = float(
                (ctx.vix.iloc[-1] - ctx.vix.iloc[-252:].mean()) / max(ctx.vix.iloc[-252:].std(), 1e-6)
            ) if len(ctx.vix) > 10 else 0.0
            # 3-month mean IC per factor (from running window)
            ic_3mo = self._compute_rolling_ic_3mo()

            # --- STATIC ---
            fw_static, regime, vix_pct, disp = static_engine.compute(z_ortho, ctx.vix, use_hmm=use_hmm)
            # --- DYNAMIC (grid) ---
            fw_dyn, _, _, _ = dynamic_engine.compute(z_ortho, ctx.vix, use_hmm=use_hmm)
            # --- BANDIT ---
            fw_bandit, arm_idx, ctx_vec = bandit_engine.select(
                vix_level_z=vix_z,
                regime_flag=regime.value,
                dispersion=disp.to_dict(),
                ic_3mo=ic_3mo,
            )

            # 4) Compose weights & backtest each variant
            for strategy, fweights in (
                (Strategy.STATIC, fw_static),
                (Strategy.DYNAMIC_GRID, fw_dyn),
                (Strategy.DYNAMIC_BANDIT, fw_bandit),
            ):
                if strategy not in strategies_to_run:
                    continue
                composite = self.zscore_engine.composite(z_ortho, fweights)
                longs, shorts = self._long_short_symbols(
                    composite,
                    prev_longs=self._prev_longs.get(strategy),
                    prev_shorts=self._prev_shorts.get(strategy),
                )
                longs = self._apply_momentum_filter(longs, raw["momentum"])
                self._prev_longs[strategy] = list(longs)
                self._prev_shorts[strategy] = list(shorts)
                composite_series = composite.copy()
                # Construct per-leg weights
                trailing_ret = ctx.returns_usd.iloc[-self.cfg.estimation_windows.covariance_days:]
                prev_w = self._prev_weights.get(strategy, pd.Series(dtype=float))
                if self.cfg.portfolio.construction == "score_weighted":
                    # Factor-weighted allocation — task-permitted CW2 design
                    w_long = self.portfolio_engine.score_weighted_leg(longs, composite_series, is_long=True)
                    w_short = self.portfolio_engine.score_weighted_leg(shorts, composite_series, is_long=False)
                else:
                    w_long = self.portfolio_engine.optimise_leg(trailing_ret, longs, prev_w)
                    w_short = self.portfolio_engine.optimise_leg(trailing_ret, shorts, prev_w)
                # Dollar-neutral scaling: long = +0.5, short = −0.5 (→ gross = 1.0)
                w_long = w_long * 0.5
                w_short = -w_short * 0.5
                w_combined = pd.concat([w_long, w_short])

                # Compose weights: apply risk scaler
                daily_port_ret = self._simulate_daily_port_returns(
                    w_combined, ctx.returns_usd
                )
                rs = risk_scalers[strategy]
                w_scaled, risk_diag = rs.apply(w_combined, daily_port_ret)
                # Cap gross exposure at 2.0 — standard market-neutral L/S
                # (1.0 long + 1.0 short) per CW1 §3.5 spec.
                gross = w_scaled.abs().sum()
                if gross > 2.0:
                    w_scaled = w_scaled * (2.0 / gross)

                # Approx monthly return from next-context prices
                if i + 1 < len(rebalance_dates):
                    next_rb = rebalance_dates[i + 1]
                    r_gross = self._simulate_monthly_return(w_scaled, rb_date, next_rb)
                    # PLAN §6 data contract + §8.4 L/S decomposition — the
                    # canonical DYNAMIC_GRID book reports its long-leg and
                    # short-leg realised monthly returns separately, so the
                    # report's Figure 12 (L/S decomposition) can be driven
                    # from portfolio_returns rather than the exposure_log
                    # proxy alone.  Other strategies share the column via
                    # the assemble step but only DYNAMIC_GRID persists here.
                    if strategy == Strategy.DYNAMIC_GRID:
                        w_long_only = w_scaled[w_scaled > 0]
                        w_short_only = w_scaled[w_scaled < 0]
                        self._long_leg_returns[rb_date] = (
                            self._simulate_monthly_return(w_long_only, rb_date, next_rb)
                            if len(w_long_only) else 0.0
                        )
                        self._short_leg_returns[rb_date] = (
                            self._simulate_monthly_return(w_short_only, rb_date, next_rb)
                            if len(w_short_only) else 0.0
                        )
                else:
                    r_gross = 0.0

                # Compute costs
                turnover = self.cost_model.one_way_turnover(w_scaled, prev_w)
                drag_20 = self.cost_model.headline_drag(turnover)
                # Per-trade audit log (PLAN §7.9) — every symbol with a
                # non-trivial weight change is an immutable record.  Gated by
                # |Δw| > 1e-6 to suppress floating-point noise, and by strategy
                # DYNAMIC_GRID as the canonical book so the ledger doesn't
                # triple-count the variants (the variants share the same
                # signal; writing one record per trade keeps the ledger
                # auditable and PLAN-compliant).
                if strategy == Strategy.DYNAMIC_GRID:
                    self._emit_trade_ledger(
                        rb_date=rb_date,
                        new_w=w_scaled,
                        old_w=prev_w,
                        strategy=strategy,
                    )
                drag_30 = self.cost_model.sensitivity_drag(turnover)
                r_net_20 = r_gross - drag_20
                r_net_30 = r_gross - drag_30

                # Update NAV
                self._nav[strategy] *= (1 + r_net_20)
                rs.record_nav(pd.Timestamp(rb_date), self._nav[strategy])

                # Store — snapshot the pre-update weights so the cost-drag
                # calculation in _assemble_portfolio_returns_row sees the
                # correct rebalance-to-rebalance turnover (PR-6 fix).
                self._prev_weights_for_cost[strategy] = self._prev_weights.get(
                    strategy, pd.Series(dtype=float)
                ).copy()
                self._prev_weights[strategy] = w_scaled
                if strategy == Strategy.DYNAMIC_BANDIT:
                    bandit_engine.update_reward(r_net_20)
                    self._bandit_rows.append(bandit_engine.log_row(rb_date))

                # Persist weights
                for sym, w in w_scaled.items():
                    if abs(w) < 1e-8:
                        continue
                    leg = Leg.LONG if w > 0 else Leg.SHORT
                    self._weights_rows.append({
                        "date": rb_date,
                        "symbol": sym,
                        "weight": float(w),
                        "leg": leg.value,
                        "strategy": strategy.value,
                    })

                # HRP side-run (PLAN §5.3) — same factor weights & symbols,
                # HRP portfolio construction.  Written only once per
                # rebalance under DYNAMIC_GRID as the canonical book.
                if strategy == Strategy.DYNAMIC_GRID:
                    try:
                        hrp_w_long = self.portfolio_engine.optimise_leg(
                            trailing_ret, longs, self._hrp_prev_w,
                            construction_override="hrp",
                        )
                        hrp_w_short = self.portfolio_engine.optimise_leg(
                            trailing_ret, shorts, self._hrp_prev_w,
                            construction_override="hrp",
                        )
                        hrp_combined = pd.concat(
                            [hrp_w_long * 0.5, -hrp_w_short * 0.5]
                        )
                        hrp_combined = hrp_combined.groupby(hrp_combined.index).sum()
                        if i + 1 < len(rebalance_dates):
                            next_rb_h = rebalance_dates[i + 1]
                            r_hrp = self._simulate_monthly_return(
                                hrp_combined, rb_date, next_rb_h
                            )
                            to_hrp = self.cost_model.one_way_turnover(
                                hrp_combined, self._hrp_prev_w
                            )
                            self._hrp_monthly_returns[rb_date] = (
                                r_hrp - self.cost_model.headline_drag(to_hrp)
                            )
                        self._hrp_prev_w = hrp_combined
                    except Exception as exc:
                        logger.warning("HRP side-run failed at %s: %s", rb_date, exc)

                # Exposure log (only once — use dynamic_grid as canonical)
                if strategy == Strategy.DYNAMIC_GRID:
                    long_alpha = self._leg_alpha(w_scaled[w_scaled > 0], ctx, rb_date)
                    short_alpha = self._leg_alpha(-w_scaled[w_scaled < 0], ctx, rb_date)
                    port_beta = self._compute_portfolio_beta(daily_port_ret, rb_date)
                    self._exposure_rows.append({
                        "date": rb_date,
                        "gross_exposure": float(w_scaled.abs().sum()),
                        "net_exposure": float(w_scaled.sum()),
                        "portfolio_beta": port_beta,   # empirical β vs ^GSPC over trailing 252 days
                        "var_99": risk_diag["var_99"],
                        "es_99": risk_diag["es_99"],
                        "position_scale": risk_diag["position_scale"],
                        "vol_target_scalar": risk_diag["vol_target_scalar"],
                        "dd_control_scalar": risk_diag["dd_control_scalar"],
                        "drawdown_12m": risk_diag["drawdown_12m"],
                        "turnover_1way": turnover,
                        "cost_drag_20bp": drag_20,
                        "cost_drag_30bp": drag_30,
                        "long_alpha": long_alpha,
                        "short_alpha": short_alpha,
                        "hhi_concentration": float((w_scaled ** 2).sum()),
                        "n_stocks_long": int((w_scaled > 0).sum()),
                        "n_stocks_short": int((w_scaled < 0).sum()),
                        "n_stocks_filtered_liquidity": len(ctx.universe.symbols),  # approx
                        "n_stocks_filtered_htb": 0,
                    })

            # Portfolio returns (aggregated)
            if i + 1 < len(rebalance_dates):
                next_rb = rebalance_dates[i + 1]
                # Rebalance EW benchmark at rb_date using the *filtered* universe
                self._ew_bench_weights_prev = ew_bench.rebalance(ctx.universe.symbols)
                rets = self._assemble_portfolio_returns_row(
                    rb_date, next_rb, strategies_to_run, ctx, bandit_engine,
                    ew_bench=ew_bench, spx_bench=spx_bench, blend_bench=blend_bench,
                )
                self._returns_rows.append(rets)

            # Regime + factor scores
            self._regime_rows.append({
                "date": rb_date,
                "vix_level": float(ctx.vix.iloc[-1]) if len(ctx.vix) else 0.0,
                "vix_percentile": float(vix_pct),
                "regime_pct": regime.value,
                "regime_hmm": None,
                "hmm_prob_high": None,
                "dispersion_momentum": float(disp.get("momentum", 0.0)),
                "dispersion_value": float(disp.get("value", 0.0)),
                "dispersion_quality": float(disp.get("quality", 0.0)),
                "dispersion_sentiment": float(disp.get("sentiment", 0.0)),
                "w_momentum": fw_dyn.get("momentum", 0.3),
                "w_value": fw_dyn.get("value", 0.3),
                "w_quality": fw_dyn.get("quality", 0.25),
                "w_sentiment": fw_dyn.get("sentiment", 0.15),
            })

            # Factor scores (per stock)
            composite_dyn = self.zscore_engine.composite(z_ortho, fw_dyn)
            for sym in z_ortho.index:
                self._factor_rows.append({
                    "date": rb_date,
                    "symbol": sym,
                    "gics_sector": ctx.universe.gics_map.get(sym, "Unknown"),
                    "momentum_z": float(z_raw.loc[sym, "momentum"]) if sym in z_raw.index else 0.0,
                    "value_z": float(z_raw.loc[sym, "value"]) if sym in z_raw.index else 0.0,
                    "quality_z": float(z_raw.loc[sym, "quality"]) if sym in z_raw.index else 0.0,
                    "sentiment_z": float(z_raw.loc[sym, "sentiment"]) if sym in z_raw.index else 0.0,
                    "momentum_z_ortho": float(z_ortho.loc[sym, "momentum"]) if sym in z_ortho.index else 0.0,
                    "value_z_ortho": float(z_ortho.loc[sym, "value"]) if sym in z_ortho.index else 0.0,
                    "quality_z_ortho": float(z_ortho.loc[sym, "quality"]) if sym in z_ortho.index else 0.0,
                    "sentiment_z_ortho": float(z_ortho.loc[sym, "sentiment"]) if sym in z_ortho.index else 0.0,
                    "composite_z": float(composite_dyn.loc[sym]) if sym in composite_dyn.index else 0.0,
                })

            self._prev_factor_z = z_ortho
            prev_ctx = ctx

            logger.info("[%d/%d] %s  regime=%s  vix=%.2f  nav_dyn=%.4f",
                        i + 1, len(rebalance_dates), rb_date, regime.value,
                        float(ctx.vix.iloc[-1]) if len(ctx.vix) else float("nan"),
                        self._nav.get(Strategy.DYNAMIC_GRID, 1.0))

        # --- Finalise ---
        return BacktestResult(
            returns=pd.DataFrame(self._returns_rows),
            weights=pd.DataFrame(self._weights_rows),
            factor_scores=pd.DataFrame(self._factor_rows),
            factor_ic=pd.DataFrame(self._ic_rows),
            factor_premia=pd.DataFrame(self._premia_rows),
            regime_log=pd.DataFrame(self._regime_rows),
            exposure_log=pd.DataFrame(self._exposure_rows),
            bandit_log=pd.DataFrame(self._bandit_rows),
            trade_ledger=pd.DataFrame(self._ledger_rows),
            config_hash=self.cfg.config_hash(),
            data_snapshot_sha256=self.data_loader.data_snapshot_sha256(),
            git_sha=self.cfg.git_sha(),
            seed=self.cfg.backtest.random_seed,
        )

    # ------------------------------------------------------------------
    def _compute_rolling_ic_3mo(self) -> dict[str, float]:
        """3-month mean IC per factor from the running IC log."""
        if not self._ic_rows:
            return {"momentum": 0.0, "value": 0.0, "quality": 0.0, "sentiment": 0.0}
        df = pd.DataFrame(self._ic_rows).tail(12)  # 3 months ~ 3 rebalances = 3 factors/date
        out = {}
        for f in ["momentum", "value", "quality", "sentiment"]:
            sub = df[df["factor"] == f]
            out[f] = float(sub["ic_spearman"].tail(3).mean()) if len(sub) else 0.0
        return out

    # ------------------------------------------------------------------
    def _simulate_daily_port_returns(
        self, weights: pd.Series, returns: pd.DataFrame
    ) -> pd.Series:
        aligned = returns.reindex(columns=weights.index, fill_value=0.0).fillna(0.0)
        return aligned @ weights

    # ------------------------------------------------------------------
    def _emit_trade_ledger(
        self,
        rb_date: date,
        new_w: pd.Series,
        old_w: pd.Series,
        strategy: Strategy,
    ) -> None:
        """Emit one ``TradeLedgerRow`` per non-trivial weight change (PLAN §7.9).

        Called once per (rebalance_date × strategy) with the *pre-* and *post-*
        rebalance weight books for that strategy.  ``rebalance_id`` is a UUID4
        so every rebalance is uniquely identifiable in post-hoc audit.
        """
        if new_w is None or len(new_w) == 0:
            return
        old = old_w if old_w is not None else pd.Series(dtype=float)
        all_syms = new_w.index.union(old.index)
        new_aligned = new_w.reindex(all_syms, fill_value=0.0)
        old_aligned = old.reindex(all_syms, fill_value=0.0)
        delta = new_aligned - old_aligned
        traded = delta.abs() > 1e-6
        if not traded.any():
            return
        rebalance_id = uuid.uuid4().hex
        nav = float(self._nav.get(strategy, self._initial_nav()))
        cost_bp = self.cfg.costs.cost_per_side_bp_headline
        for sym in all_syms[traded]:
            nw = float(new_aligned.loc[sym])
            ow = float(old_aligned.loc[sym])
            dw = nw - ow
            if abs(ow) < 1e-8 and abs(nw) >= 1e-8:
                action = "open"
            elif abs(nw) < 1e-8 and abs(ow) >= 1e-8:
                action = "close"
            else:
                action = "adjust"
            side = "long" if (nw if abs(nw) >= 1e-8 else ow) > 0 else "short"
            notional = abs(dw) * nav
            # Simple sqrt-law market-impact stub (Almgren-Chriss 2001).  Full
            # Kyle-λ / Amihud estimate lives in engine/attribution.py §5.11 and
            # is written to a separate capacity report; here we record a
            # reasonable per-trade predicted impact.
            predicted_impact_bp = float(5.0 * np.sqrt(abs(dw)))
            self._ledger_rows.append({
                "date": rb_date,
                "symbol": sym,
                "side": side,
                "action": action,
                "old_weight": ow,
                "new_weight": nw,
                "notional_usd": notional,
                "predicted_impact_bp": predicted_impact_bp,
                "proportional_cost_bp": float(cost_bp),
                "leg_id": f"{strategy.value}_{side}",
                "rebalance_id": rebalance_id,
                "seed": self.cfg.backtest.random_seed,
                "data_snapshot_sha256": self._data_snapshot_sha256 or "unknown",
            })

    # ------------------------------------------------------------------
    def _compute_portfolio_beta(
        self, daily_port_ret: pd.Series, rb_date: date, lookback: int = 252
    ) -> float:
        """Empirical portfolio β vs ^GSPC over trailing ``lookback`` days.

        Replaces the previous ``portfolio_beta = 0.0`` stub flagged by the
        CW2 audit (B3 / §3).  Computed via paired-period OLS:

            β = Cov(r_port, r_SPX) / Var(r_SPX)

        Per PLAN §8.4, the design target is ``|β| ≤ 0.1`` — writing an
        empirical estimate here is what lets the report reference the target
        versus a realised number rather than a hard-coded claim.
        """
        r = daily_port_ret.dropna()
        if len(r) < 20:
            return 0.0
        try:
            spx = self.data_loader.load_benchmark(rb_date, lookback, "^GSPC")
        except Exception as exc:
            logger.warning("SPX load for beta at %s failed: %s", rb_date, exc)
            return 0.0
        if len(spx) < 20:
            return 0.0
        spx_ret = spx.pct_change().dropna()
        # Align on intersection of dates
        df = pd.concat([r.rename("port"), spx_ret.rename("spx")], axis=1).dropna()
        if len(df) < 20:
            return 0.0
        cov = float(df["port"].cov(df["spx"]))
        var = float(df["spx"].var())
        if var <= 1e-12:
            return 0.0
        return cov / var

    # ------------------------------------------------------------------
    def _simulate_monthly_return(
        self, weights: pd.Series, rb_date: date, next_rb_date: date
    ) -> float:
        """Realised USD-converted return between rb_date and next_rb_date.

        CW1 Eq. 2.5 applied per non-USD stock:  R_USD = (1+R_local)(FX_t/FX_{t-1}) - 1.
        """
        from sqlalchemy import text
        symbols = weights.index.tolist()
        if not symbols:
            return 0.0
        query = text(
            f"""
            SELECT cob_date, symbol, adj_close_price
            FROM {self.data_loader._schema}.daily_prices
            WHERE cob_date >= :start AND cob_date <= :end
              AND symbol = ANY(:syms) AND adj_close_price IS NOT NULL
            """
        )
        df = pd.read_sql(query, self.data_loader._engine,
                         params={"start": rb_date, "end": next_rb_date, "syms": symbols})
        if df.empty:
            return 0.0
        wide = df.pivot_table(index="cob_date", columns="symbol",
                              values="adj_close_price").sort_index()
        if len(wide) < 2:
            return 0.0

        p_start = wide.iloc[0]
        p_end = wide.iloc[-1]
        r_local = (p_end / p_start - 1).reindex(weights.index, fill_value=0.0)

        # FX conversion for non-USD stocks
        universe = self.data_loader.load_universe(rb_date)
        from engine.data_loader import currency_to_fx_pair
        from datetime import timedelta as _td
        fx = self.data_loader.load_fx(next_rb_date + _td(days=1), 90)
        r_usd = r_local.copy()
        for sym in weights.index:
            ccy = universe.currency_map.get(sym, "USD")
            pair = currency_to_fx_pair(ccy)
            if pair is None or pair not in fx.columns:
                continue
            fx_slice = fx[pair].ffill()
            try:
                fx_start = float(fx_slice.asof(pd.Timestamp(rb_date)))
                fx_end = float(fx_slice.asof(pd.Timestamp(next_rb_date)))
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("FX asof lookup failed for %s [%s]: %s", pair, sym, exc)
                continue
            if pd.isna(fx_start) or pd.isna(fx_end) or fx_start <= 0:
                continue
            fx_ratio = fx_end / fx_start
            r_usd[sym] = (1 + r_local[sym]) * fx_ratio - 1

        return float((weights * r_usd).sum())

    # ------------------------------------------------------------------
    def _assemble_portfolio_returns_row(
        self,
        rb_date: date,
        next_rb_date: date,
        strategies: tuple[Strategy, ...],
        ctx: PITContext,
        bandit_engine: BanditWeights,
        ew_bench: Optional[EqualWeightBenchmark] = None,
        spx_bench: Optional[SPXBenchmark] = None,
        blend_bench: Optional[CashMarketBlend] = None,
    ) -> dict:
        """Compute one row of the portfolio_returns.parquet contract."""
        row: dict[str, Any] = {"date": rb_date}
        # For each strategy, compute gross+net — we reuse the most recent
        # weight entries already stored
        for s in strategies:
            w = self._prev_weights.get(s, pd.Series(dtype=float))
            if len(w) == 0:
                gross = 0.0
            else:
                gross = self._simulate_monthly_return(w, rb_date, next_rb_date)
            turnover = self._recent_turnover(s)
            net20 = gross - self.cost_model.headline_drag(turnover)
            net30 = gross - self.cost_model.sensitivity_drag(turnover)
            if s == Strategy.DYNAMIC_GRID:
                row["dynamic_gross"] = gross
                row["dynamic_net_20bp"] = net20
                row["dynamic_net_30bp"] = net30
            elif s == Strategy.STATIC:
                row["static_net_20bp"] = net20
                row["static_net_30bp"] = net30
            elif s == Strategy.DYNAMIC_BANDIT:
                row["bandit_net_20bp"] = net20
        # Defaults
        row.setdefault("dynamic_gross", 0.0)
        row.setdefault("dynamic_net_20bp", 0.0)
        row.setdefault("dynamic_net_30bp", 0.0)
        row.setdefault("static_net_20bp", 0.0)
        row.setdefault("static_net_30bp", 0.0)
        # HRP robustness-comparison book (PLAN §5.3)
        row["hrp_net_20bp"] = self._hrp_monthly_returns.get(rb_date, None)
        row.setdefault("bandit_net_20bp", None)
        # Benchmarks (PLAN §7.13 + user-requested advanced multi-benchmark)
        if ew_bench is not None and self._ew_bench_weights_prev is not None:
            row["benchmark_ew"] = ew_bench.period_return(
                rb_date, next_rb_date, self._ew_bench_weights_prev
            )
        elif len(ctx.returns_usd):
            # Fallback
            row["benchmark_ew"] = float(ctx.returns_usd.iloc[-21:].mean(axis=1).sum())
        else:
            row["benchmark_ew"] = 0.0
        if spx_bench is not None:
            row["benchmark_spx"] = spx_bench.period_return(rb_date, next_rb_date)
        else:
            row["benchmark_spx"] = 0.0
        if blend_bench is not None:
            row["benchmark_cash_market_50_50"] = blend_bench.period_return(rb_date, next_rb_date)
        else:
            row["benchmark_cash_market_50_50"] = 0.0
        # L/S leg realised monthly returns (PLAN §6 contract + §8.4)
        row["long_leg"] = self._long_leg_returns.get(rb_date, 0.0)
        row["short_leg"] = self._short_leg_returns.get(rb_date, 0.0)
        row["rf_rate"] = ctx.rf_rate / 12.0      # monthly
        return row

    # ------------------------------------------------------------------
    def _recent_turnover(self, strategy: Strategy) -> float:
        """One-way turnover for the just-completed rebalance.

        Called from ``_assemble_portfolio_returns_row`` to compute the
        cost-drag component of the net-return columns in
        ``portfolio_returns.parquet``.

        **Bug fix (PR-6 / #2):** previously compared the new weights to an
        empty ``Series``, which makes ``one_way_turnover`` equal to
        ``0.5 × sum(|w|) ≈ 1.0`` for a dollar-neutral book.  That inflated
        the cost drag in the net-return columns vs. the main-loop NAV
        update (which correctly used the pre-update weights) and caused
        the exposure-log ``turnover_1way`` (actual ~ 0.71) to diverge
        from the cost implied by the net-return columns.  Now compares
        the current weights against ``_prev_weights_for_cost`` (the
        pre-update snapshot), matching the main-loop calculation exactly.
        """
        w_new = self._prev_weights.get(strategy, pd.Series(dtype=float))
        w_prev = self._prev_weights_for_cost.get(strategy, pd.Series(dtype=float))
        return self.cost_model.one_way_turnover(w_new, w_prev)

    # ------------------------------------------------------------------
    def _leg_alpha(self, leg_weights: pd.Series, ctx: PITContext, rb_date: date) -> float:
        """Leg mean-return minus benchmark mean-return (Viz Ref §1.4)."""
        if len(leg_weights) == 0:
            return 0.0
        w = leg_weights / leg_weights.abs().sum()
        r_leg = float((ctx.returns_usd.iloc[-21:].reindex(columns=w.index, fill_value=0.0) @ w).mean())
        r_bench = float(ctx.returns_usd.iloc[-21:].mean(axis=1).mean())
        return r_leg - r_bench


__all__ = ["BacktestEngine", "BacktestResult", "monthly_rebalance_dates"]
