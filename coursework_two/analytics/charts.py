"""Charts module — all 14 mandated visualisations plus 3 extensions.

Strictly follows the Viz Reference Part 2 Visual Style Guide:
    Navy #1B2A4A (dynamic) · Blue #2E75B6 (static/comparison) ·
    Grey #7F8C8D (benchmark, dotted) · Red #C0392B (drawdown, losses) ·
    Green #27AE60 (positive).
    Factor colours: Momentum=Navy, Value=Blue, Quality=Green, Sentiment=Orange.
    Figure sizes: 12×5.5 (time-series), 7×5.5 (heatmaps), 8×5 (bar charts).
    150 DPI on-screen, 300 DPI for submission.
    Dates: "MMM 'YY"; Percent axes via PercentFormatter.
"""

from __future__ import annotations

from typing import Optional

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

# =============================================================================
# Palette (PLAN §9 + Viz Ref §2.8)
# =============================================================================
PAL = {
    "dynamic": "#1B2A4A",       # Navy
    "static": "#2E75B6",         # Blue
    "benchmark": "#7F8C8D",      # Grey
    "loss": "#C0392B",           # Red
    "gain": "#27AE60",           # Green
    "momentum": "#1B2A4A",
    "value": "#2E75B6",
    "quality": "#27AE60",
    "sentiment": "#E67E22",      # Orange
    "bandit": "#8E44AD",         # Purple (extension)
    "hrp": "#16A085",            # Teal (extension)
}
STYLE_GRID = dict(linestyle="--", alpha=0.3)

# Default style
mpl.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "figure.dpi": 150,
    "savefig.dpi": 300,
})


def _format_dates(ax, df_index):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.grid(True, **STYLE_GRID)


# =============================================================================
# Fig 1 — Cumulative Return
# =============================================================================
def plot_cumulative_return(returns_df: pd.DataFrame) -> Figure:
    """Three-line plot: dynamic (navy, filled) / static (blue dashed) / benchmark (grey dotted).
    Growth-of-$1; horizontal reference at 1.0.
    """
    df = returns_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    fig, ax = plt.subplots(figsize=(12, 5.5))
    cum_dyn = (1 + df["dynamic_net_20bp"].fillna(0)).cumprod()
    cum_sta = (1 + df["static_net_20bp"].fillna(0)).cumprod()
    cum_bch = (1 + df["benchmark_ew"].fillna(0)).cumprod()
    ax.plot(cum_dyn.index, cum_dyn.values, color=PAL["dynamic"], lw=2.2, label="Strategy (Dynamic Weights)")
    ax.plot(cum_sta.index, cum_sta.values, color=PAL["static"], lw=1.8, ls="--", label="Strategy (Static Weights)")
    ax.plot(cum_bch.index, cum_bch.values, color=PAL["benchmark"], lw=1.6, ls=":", label="Equal-Weight Benchmark")
    ax.fill_between(cum_dyn.index, 1.0, cum_dyn.values, where=(cum_dyn.values >= 1.0),
                    color=PAL["dynamic"], alpha=0.08)
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_ylabel("Growth of $1")
    ax.set_title("Cumulative Return: Strategy vs Benchmark")
    ax.legend(loc="upper left", frameon=False)
    _format_dates(ax, cum_dyn.index)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 2 — Drawdown (underwater)
# =============================================================================
def plot_drawdown(dd_series: pd.Series) -> Figure:
    s = dd_series.copy()
    s.index = pd.to_datetime(s.index)
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.fill_between(s.index, 0, s.values * 100, where=(s.values < 0),
                    color=PAL["loss"], alpha=0.4)
    ax.plot(s.index, s.values * 100, color=PAL["loss"], lw=1.2)
    idx_min = s.idxmin()
    val_min = s.min() * 100
    ax.annotate(
        f"Max DD {val_min:.1f}%",
        xy=(idx_min, val_min),
        xytext=(10, -15),
        textcoords="offset points",
        color=PAL["loss"],
        fontsize=9,
        arrowprops=dict(arrowstyle="->", color=PAL["loss"], lw=0.8),
    )
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("Drawdown (%)")
    ax.set_title("Drawdown (Underwater Plot) — Dynamic Strategy")
    _format_dates(ax, s.index)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 3 — VIX regime overlay with monthly returns
# =============================================================================
def plot_vix_regime_returns(
    returns_df: pd.DataFrame, regime_df: pd.DataFrame
) -> Figure:
    df = returns_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    reg = regime_df.copy()
    reg["date"] = pd.to_datetime(reg["date"])
    reg = reg.set_index("date").sort_index()

    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax2 = ax.twinx()
    r = df["dynamic_net_20bp"].fillna(0) * 100
    colors = [PAL["gain"] if x >= 0 else PAL["loss"] for x in r]
    ax.bar(r.index, r.values, color=colors, width=20, alpha=0.85)
    ax2.plot(reg.index, reg["vix_level"].values, color="#E67E22", lw=1.6, label="VIX Level")

    # Regime shading
    regime_colors = {"low": PAL["gain"], "normal": "#F5B041", "high": PAL["loss"]}
    current_regime = None
    span_start = None
    for idx, row in reg.iterrows():
        if row["regime_pct"] != current_regime:
            if current_regime is not None:
                ax.axvspan(span_start, idx, alpha=0.06,
                           color=regime_colors.get(current_regime, "white"))
            current_regime = row["regime_pct"]
            span_start = idx
    if current_regime is not None and span_start is not None:
        ax.axvspan(span_start, reg.index[-1], alpha=0.06,
                   color=regime_colors.get(current_regime, "white"))

    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("Monthly Return (%)")
    ax2.set_ylabel("VIX")
    ax.set_title("Monthly Returns by VIX Regime")
    ax2.legend(loc="upper right", frameon=False)
    _format_dates(ax, r.index)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 4 — γ×λ parameter sensitivity heatmap
# =============================================================================
def plot_param_sensitivity(grid_df: pd.DataFrame, metric: str = "sharpe_net") -> Figure:
    """Heatmap with annotated cells and bordered optimal cell."""
    pivot = grid_df.pivot_table(
        index="gamma", columns="lambda_magnitude", values=metric, aggfunc="mean"
    )
    fig, ax = plt.subplots(figsize=(7, 5.5))
    im = ax.imshow(pivot.values, cmap="YlOrRd", aspect="auto", origin="lower")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f"±{int(c*100)}%" for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{g:.2f}" for g in pivot.index])
    ax.set_xlabel("Regime Tilt Magnitude (λ)")
    ax.set_ylabel("Dispersion Sensitivity (γ)")
    ax.set_title(f"Parameter Sensitivity: γ (Dispersion) × λ (Regime Tilt)")
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="black", fontsize=9)
    # Bordered optimal cell
    flat = pivot.values.flatten()
    if not np.all(pd.isna(flat)):
        best = np.nanargmax(flat)
        bi, bj = np.unravel_index(best, pivot.shape)
        rect = plt.Rectangle((bj - 0.48, bi - 0.48), 0.96, 0.96,
                             fill=False, edgecolor="black", lw=2.0)
        ax.add_patch(rect)
    plt.colorbar(im, ax=ax, label=metric.replace("_", " ").title())
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 5 — Rolling Information Coefficient per factor
# =============================================================================
def plot_rolling_ic(ic_df: pd.DataFrame, window: int = 3) -> Figure:
    df = ic_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for factor, sub in df.groupby("factor"):
        s = sub.set_index("date")["ic_spearman"].rolling(window).mean()
        ax.plot(
            s.index, s.values,
            color=PAL.get(factor, "black"), lw=1.7, label=factor.title(),
            ls="--" if factor in ("quality", "sentiment") else "-",
        )
    ax.axhline(0.05, color="grey", lw=0.8, ls=":", alpha=0.7)
    ax.text(df["date"].iloc[0], 0.055, "IC = 0.05 (strong signal)", color="grey", fontsize=8)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("Spearman IC")
    ax.set_title(f"Rolling Information Coefficient by Factor ({window}-Month Avg)")
    ax.legend(loc="best", frameon=False)
    _format_dates(ax, df["date"])
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 6 — Annual factor return attribution (stacked bar + total)
# =============================================================================
def plot_factor_attribution(contrib_df: pd.DataFrame) -> Figure:
    """contrib_df: columns year, factor, contribution."""
    pivot = contrib_df.pivot_table(index="year", columns="factor", values="contribution", aggfunc="sum")
    # Reorder
    order = [f for f in ["momentum", "value", "quality", "sentiment"] if f in pivot.columns]
    pivot = pivot[order]
    fig, ax = plt.subplots(figsize=(8, 5))
    bottoms = np.zeros(len(pivot))
    for f in order:
        vals = pivot[f].values * 100
        ax.bar(pivot.index.astype(str), vals, bottom=bottoms, label=f.title(), color=PAL.get(f, "grey"))
        bottoms += vals
    totals = pivot.sum(axis=1).values * 100
    ax.plot(pivot.index.astype(str), totals, color="black", marker="o", lw=1.4, label="Total Return")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("Return Contribution (%)")
    ax.set_title("Annual Factor Return Attribution")
    ax.legend(loc="best", frameon=False)
    ax.grid(True, axis="y", **STYLE_GRID)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 7 — Rolling 12-month Sharpe
# =============================================================================
def plot_rolling_sharpe(returns_df: pd.DataFrame, window: int = 12) -> Figure:
    df = returns_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    r = df["dynamic_net_20bp"].fillna(0)
    roll_mean = r.rolling(window).mean() * 12
    roll_std = r.rolling(window).std() * np.sqrt(12)
    roll_sr = roll_mean / roll_std.replace(0, np.nan)
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(roll_sr.index, roll_sr.values, color=PAL["dynamic"], lw=2.0, label="Rolling 12M Sharpe")
    ax.axhline(0, color="black", lw=0.8)
    ax.axhline(1.0, color=PAL["gain"], lw=0.8, ls=":", alpha=0.7)
    ax.set_ylabel("Sharpe Ratio")
    ax.set_title("Rolling 12-Month Sharpe Ratio (Dynamic Net 20bp)")
    ax.legend(loc="best", frameon=False)
    _format_dates(ax, roll_sr.index)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 8 — COVID zoom (drawdown + VIX + regime)
# =============================================================================
def plot_covid_zoom(returns_df: pd.DataFrame, regime_df: pd.DataFrame) -> Figure:
    return plot_vix_regime_returns(
        returns_df[(returns_df["date"] >= "2020-02-01") & (returns_df["date"] <= "2020-07-01")]
        if not returns_df.empty else returns_df,
        regime_df[(regime_df["date"] >= "2020-02-01") & (regime_df["date"] <= "2020-07-01")]
        if not regime_df.empty else regime_df,
    )


# =============================================================================
# Fig 9 — Cost scenarios (gross vs net-20 vs net-30)
# =============================================================================
def plot_cost_comparison(returns_df: pd.DataFrame) -> Figure:
    df = returns_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for col, color, label in (
        ("dynamic_gross", PAL["dynamic"], "Gross"),
        ("dynamic_net_20bp", PAL["static"], "Net (20bp/side)"),
        ("dynamic_net_30bp", PAL["loss"], "Net (30bp/side)"),
    ):
        if col not in df.columns:
            continue
        cum = (1 + df[col].fillna(0)).cumprod()
        ax.plot(cum.index, cum.values, color=color, lw=1.9, label=label)
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_ylabel("Growth of $1")
    ax.set_title("Net of Costs: 20bp vs 30bp per Side")
    ax.legend(loc="best", frameon=False)
    _format_dates(ax, df.index)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 10 — Sector exposure heatmap
# =============================================================================
def plot_sector_exposure(weights_df: pd.DataFrame) -> Figure:
    df = weights_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    # Need sector — join via placeholder if missing
    if "gics_sector" not in df.columns:
        df["gics_sector"] = "Unknown"
    df["signed_w"] = df["weight"]  # already signed in weights_df (short_w < 0)
    pivot = df.pivot_table(index="gics_sector", columns="date", values="signed_w", aggfunc="sum").fillna(0)
    fig, ax = plt.subplots(figsize=(7, 5.5))
    im = ax.imshow(pivot.values, cmap="RdBu_r", aspect="auto", vmin=-0.15, vmax=0.15)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index, fontsize=8)
    ax.set_xticks(range(0, len(pivot.columns), max(1, len(pivot.columns) // 6)))
    ax.set_xticklabels(
        [pd.Timestamp(c).strftime("%b '%y") for c in pivot.columns[::max(1, len(pivot.columns)//6)]],
        fontsize=8,
    )
    ax.set_title("Sector Exposure Over Time")
    plt.colorbar(im, ax=ax, label="Net sector weight")
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 11 — Turnover time-series
# =============================================================================
def plot_turnover(exposure_df: pd.DataFrame) -> Figure:
    df = exposure_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(df["date"], df["turnover_1way"] * 100, color=PAL["dynamic"], lw=1.8, marker="o")
    ax.set_ylabel("1-way Turnover (%)")
    ax.set_title("Monthly 1-Way Turnover")
    ax.grid(True, **STYLE_GRID)
    _format_dates(ax, df["date"])
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 12 — Long vs Short leg decomposition
# =============================================================================
def plot_ls_decomposition(exposure_df: pd.DataFrame) -> Figure:
    df = exposure_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["year"] = df["date"].dt.year
    annual = df.groupby("year").agg({"long_alpha": "sum", "short_alpha": "sum"})
    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(annual))
    ax.bar(x, annual["long_alpha"] * 100, color=PAL["gain"], label="Long-leg α")
    ax.bar(x, annual["short_alpha"] * 100, bottom=annual["long_alpha"] * 100,
           color=PAL["loss"], label="Short-leg α")
    ax.set_xticks(x)
    ax.set_xticklabels(annual.index.astype(str))
    ax.set_ylabel("Annual α (%)")
    ax.set_title("Long vs Short Leg Alpha Decomposition (Annual)")
    ax.legend(loc="best", frameon=False)
    ax.axhline(0, color="black", lw=0.8)
    ax.grid(True, axis="y", **STYLE_GRID)
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 13 — Ablation bar chart
# =============================================================================
def plot_ablation(ablation_df: pd.DataFrame, metric: str = "sharpe_net") -> Figure:
    df = ablation_df.copy().sort_values(metric, ascending=False)
    fig, ax = plt.subplots(figsize=(8, 5))
    colors = [PAL["dynamic"] if "full" in str(v).lower() else PAL["static"] for v in df["variant"]]
    ax.bar(df["variant"], df[metric], color=colors)
    ax.set_ylabel(metric.replace("_", " ").title())
    ax.set_title("Ablation — Sharpe with Each Factor Removed")
    ax.axhline(0, color="black", lw=0.8)
    ax.grid(True, axis="y", **STYLE_GRID)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.tight_layout()
    return fig


# =============================================================================
# Fig 14 — Covariance heatmap
# =============================================================================
def plot_covariance(cov_matrix: pd.DataFrame, title: str = "Ledoit-Wolf Shrunk Covariance") -> Figure:
    fig, ax = plt.subplots(figsize=(7, 5.5))
    im = ax.imshow(cov_matrix.values, cmap="RdBu_r")
    ax.set_title(title)
    plt.colorbar(im, ax=ax)
    fig.tight_layout()
    return fig


# =============================================================================
# Extensions (PLAN §9.15–17)
# =============================================================================
def plot_deflated_sharpe_distribution(bootstrap_df: pd.DataFrame) -> Figure:
    """Extension 15: bootstrap Sharpe distribution with 95% CI band."""
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.hist(bootstrap_df["sharpe"].dropna(), bins=40, color=PAL["dynamic"], alpha=0.85, edgecolor="white")
    q5, q95 = np.nanquantile(bootstrap_df["sharpe"], [0.025, 0.975])
    ax.axvline(q5, color=PAL["loss"], ls="--", lw=1.4, label=f"2.5% = {q5:.2f}")
    ax.axvline(q95, color=PAL["loss"], ls="--", lw=1.4, label=f"97.5% = {q95:.2f}")
    ax.axvline(bootstrap_df["sharpe"].mean(), color=PAL["gain"], lw=1.8, label=f"Mean = {bootstrap_df['sharpe'].mean():.2f}")
    ax.set_title("Block-Bootstrap Sharpe Distribution (Politis-Romano 1994)")
    ax.set_xlabel("Sharpe Ratio")
    ax.set_ylabel("Frequency")
    ax.legend(loc="best", frameon=False)
    ax.grid(True, **STYLE_GRID)
    fig.tight_layout()
    return fig


def plot_ff5_regression_loadings(regression_df: pd.DataFrame) -> Figure:
    """Extension 16: FF5+Mom regression β with Newey-West 95% CI bars."""
    df = regression_df.copy()
    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(df))
    lower = df["beta"] - 1.96 * df["se_nw"]
    upper = df["beta"] + 1.96 * df["se_nw"]
    colors = [PAL["gain"] if b > 0 else PAL["loss"] for b in df["beta"]]
    ax.bar(x, df["beta"], color=colors, yerr=[df["beta"] - lower, upper - df["beta"]], capsize=4)
    ax.set_xticks(x)
    ax.set_xticklabels(df["factor"], rotation=30, ha="right")
    ax.set_ylabel("Beta (monthly)")
    ax.set_title("FF5 + Momentum Regression Loadings (Newey-West HAC)")
    ax.axhline(0, color="black", lw=0.8)
    ax.grid(True, axis="y", **STYLE_GRID)
    fig.tight_layout()
    return fig


def plot_bandit_posterior(bandit_log_df: pd.DataFrame) -> Figure:
    """Extension 17: Arm posterior-mean trajectory over time (TS learning)."""
    df = bandit_log_df.copy()
    if df.empty:
        fig, ax = plt.subplots(figsize=(12, 5.5))
        ax.set_title("Bandit log is empty — no data to plot")
        return fig
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    import json

    K = len(json.loads(df.iloc[0]["arm_posterior_mean_json"]))
    posteriors = np.stack([
        np.mean(np.array(json.loads(r)), axis=1)   # mean across context dims per arm
        for r in df["arm_posterior_mean_json"]
    ])
    fig, ax = plt.subplots(figsize=(12, 5.5))
    cmap = plt.get_cmap("tab20", K)
    arm_labels = [
        "Static", "Mom+45", "Val+45", "Qual+45", "Sent+45",
        "VIX-Low", "VIX-Norm", "VIX-High", "Mom+Val", "Qual-Heavy", "Sent-Low", "EW",
    ]
    for a in range(K):
        label = arm_labels[a] if a < len(arm_labels) else f"Arm {a}"
        ax.plot(df["date"], posteriors[:, a], color=cmap(a), lw=1.3, label=label)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("Posterior Mean Reward (collapsed)")
    ax.set_title("Thompson Sampling — Arm Posterior Evolution")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8)
    _format_dates(ax, df["date"])
    fig.tight_layout()
    return fig


__all__ = [
    "PAL",
    "plot_ablation",
    "plot_bandit_posterior",
    "plot_cost_comparison",
    "plot_covariance",
    "plot_covid_zoom",
    "plot_cumulative_return",
    "plot_deflated_sharpe_distribution",
    "plot_drawdown",
    "plot_factor_attribution",
    "plot_ff5_regression_loadings",
    "plot_ls_decomposition",
    "plot_param_sensitivity",
    "plot_rolling_ic",
    "plot_rolling_sharpe",
    "plot_sector_exposure",
    "plot_turnover",
    "plot_vix_regime_returns",
]
