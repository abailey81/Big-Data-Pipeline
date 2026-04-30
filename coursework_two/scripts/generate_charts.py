"""Generate all 14+ mandatory charts from saved Parquet artefacts.

Reads from ``output/`` and writes PNG/PDF files to ``charts/`` with 300 DPI.
Run AFTER a successful ``Main.py --mode full`` backtest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analytics import charts as ch
from analytics.performance import drawdown_series


def main() -> None:
    out_dir = ROOT / "output"
    chart_dir = ROOT / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    returns_df = pd.read_parquet(out_dir / "portfolio_returns.parquet")
    weights_df = pd.read_parquet(out_dir / "portfolio_weights.parquet")
    factors_df = pd.read_parquet(out_dir / "factor_scores.parquet")
    ic_df = pd.read_parquet(out_dir / "factor_ic.parquet")
    regime_df = pd.read_parquet(out_dir / "regime_log.parquet")
    exposure_df = pd.read_parquet(out_dir / "exposure_log.parquet")
    bandit_df = pd.read_parquet(out_dir / "bandit_log.parquet")

    def save(fig, name):
        path = chart_dir / f"{name}.png"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(f"  ✔ {name}.png")

    print("Rendering 14 mandatory charts + extensions…")

    # Fig 1 — Cumulative return
    save(ch.plot_cumulative_return(returns_df), "fig_01_cumulative_return")
    # Fig 2 — Drawdown
    rd = returns_df.copy()
    rd["date"] = pd.to_datetime(rd["date"])
    rd = rd.set_index("date").sort_index()
    dd = drawdown_series(rd["dynamic_net_20bp"])
    save(ch.plot_drawdown(dd), "fig_02_drawdown_underwater")
    # Fig 3 — VIX regime overlay
    save(ch.plot_vix_regime_returns(returns_df, regime_df), "fig_03_vix_regime_returns")
    # Fig 5 — Rolling IC
    if len(ic_df):
        save(ch.plot_rolling_ic(ic_df), "fig_05_rolling_ic")
    # Fig 7 — Rolling 12m Sharpe
    save(ch.plot_rolling_sharpe(returns_df), "fig_07_rolling_sharpe")
    # Fig 9 — Cost comparison
    save(ch.plot_cost_comparison(returns_df), "fig_09_cost_comparison")
    # Fig 10 — Sector exposure heatmap
    if "gics_sector" in factors_df.columns:
        gics_map = dict(zip(factors_df["symbol"], factors_df["gics_sector"]))
        wd = weights_df.copy()
        wd["gics_sector"] = wd["symbol"].map(gics_map).fillna("Unknown")
        save(ch.plot_sector_exposure(wd), "fig_10_sector_exposure")
    # Fig 11 — Turnover
    save(ch.plot_turnover(exposure_df), "fig_11_turnover")
    # Fig 12 — Long/short leg decomp
    save(ch.plot_ls_decomposition(exposure_df), "fig_12_ls_decomposition")
    # Fig 17 — Bandit posterior
    if len(bandit_df) > 1:
        save(ch.plot_bandit_posterior(bandit_df), "fig_17_bandit_posterior")

    print(f"\n✓ Charts saved to {chart_dir}/")


if __name__ == "__main__":
    main()
