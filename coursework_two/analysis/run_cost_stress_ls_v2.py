"""L/S Cost Stress v2 — correct column mapping.

Engine hardcodes columns as ``*_net_20bp`` and ``*_net_30bp``, but the
VALUES in them reflect the cost rates in ``cost_per_side_bp_headline`` and
``cost_per_side_bp_sensitivity`` respectively.  To test 4 cost levels:

  Run A:  headline=10, sensitivity=20   →  read _20bp as 10bp, _30bp as 20bp
  Run B:  headline=50, sensitivity=100  →  read _20bp as 50bp, _30bp as 100bp

v2 change (post-PR #7 review, 2026-04-24): the two stress runs now write
to a SCRATCH output directory (``analysis/_cost_stress_output/`` by
default, or whatever is passed via ``--output-dir``) instead of overwriting
``coursework_two/output/``.  This protects the pristine headline v0.3.2
parquets during the stress sweep — if an error lands mid-run there is
nothing to restore on the engine side.  The canonical 20/30 re-run at the
end is therefore no longer needed.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CW2_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(CW2_ROOT, "analytics"))

CW2 = CW2_ROOT
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")

import pandas as pd  # noqa: E402  (path setup above)
import numpy as np   # noqa: E402
from performance import (  # noqa: E402
    annualised_return, annualised_volatility, sharpe_ratio,
    max_drawdown, sortino_ratio, circular_block_bootstrap_sharpe,
)

ANN = 12
BLOCK_SIZE = 3
N_BOOTSTRAP = 2000
SEED = 42

# (headline_cost, sensitivity_cost): the two cost rates to test in one run.
COST_RUNS = [
    (10, 20),
    (50, 100),
]

VARIANTS = [("dynamic", "Dynamic L/S"), ("static", "Static L/S")]

CONFIG_PATH = os.path.join(CW2, "config", "backtest_config.yaml")
CONFIG_BACKUP = os.path.join(CW2, "config", "backtest_config.yaml.cost_stress_bak")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "L/S cost stress via two Main.py reruns (10/20 + 50/100). "
            "Writes the per-stress backtest outputs to a scratch directory "
            "so the pristine coursework_two/output/ is NOT overwritten."
        ),
    )
    p.add_argument(
        "--output-dir",
        default=os.path.join(SCRIPT_DIR, "_cost_stress_output"),
        help=(
            "Directory for the per-stress backtest parquets. Default: "
            "analysis/_cost_stress_output/. Passed through to "
            "Main.py --output-dir so pristine v0.3.2 outputs are untouched."
        ),
    )
    p.add_argument(
        "--end",
        default="2026-03-31",
        help="End date for the backtest window (default 2026-03-31).",
    )
    return p.parse_args()


def update_cost_config(headline_bp: int, sensitivity_bp: int) -> None:
    with open(CONFIG_PATH) as f:
        c = f.read()
    c = re.sub(r"cost_per_side_bp_headline:\s*\d+",
               f"cost_per_side_bp_headline: {headline_bp}", c)
    c = re.sub(r"cost_per_side_bp_sensitivity:\s*\d+",
               f"cost_per_side_bp_sensitivity: {sensitivity_bp}", c)
    with open(CONFIG_PATH, "w") as f:
        f.write(c)


def run_main(output_dir: str, end_date: str) -> None:
    print(f"  [run] python Main.py --end {end_date} --output-dir {output_dir}")
    # Prefer poetry if available, fall back to plain python3 so the script
    # still works in non-Poetry checkouts.
    have_poetry = shutil.which("poetry") is not None
    cmd = (
        ["poetry", "run", "python", "Main.py"]
        if have_poetry
        else [sys.executable, "Main.py"]
    )
    cmd += ["--end", end_date, "--output-dir", output_dir]
    r = subprocess.run(cmd, cwd=CW2, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-800:])
        raise RuntimeError(f"Main.py failed: {r.returncode}")
    print("\n".join(r.stdout.splitlines()[-4:]))


def extract(col: str, backtest_output_dir: str) -> dict:
    df = pd.read_parquet(os.path.join(backtest_output_dir, "portfolio_returns.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    ret = df[col].dropna()
    rf = df["rf_rate"].reindex(ret.index).fillna(0.0)
    raw_sr = sharpe_ratio(ret, rf_series=0.0, ann=ANN)
    excess_sr = sharpe_ratio(ret, rf_series=rf, ann=ANN)
    bs = circular_block_bootstrap_sharpe(
        returns=ret, block_size=BLOCK_SIZE, n_bootstrap=N_BOOTSTRAP, seed=SEED, ann=ANN,
    )
    return {
        "ann_return": annualised_return(ret, ann=ANN),
        "ann_vol": annualised_volatility(ret, ann=ANN),
        "raw_sharpe": raw_sr,
        "excess_sharpe": excess_sr,
        "sortino": sortino_ratio(ret, rf_series=rf, ann=ANN),
        "max_dd": max_drawdown(ret),
        "bs_ci_lo": bs["low"],
        "bs_ci_hi": bs["high"],
    }


def main() -> None:
    args = _parse_args()
    backtest_output_dir = os.path.abspath(args.output_dir)
    os.makedirs(backtest_output_dir, exist_ok=True)
    os.makedirs(MY_OUTPUT, exist_ok=True)

    print(f"Scratch backtest output: {backtest_output_dir}")
    print(f"(Pristine {os.path.join(CW2, 'output/')} will NOT be touched.)")

    shutil.copy(CONFIG_PATH, CONFIG_BACKUP)
    print(f"Config backed up -> {CONFIG_BACKUP}")

    results: list[dict] = []
    try:
        for headline_bp, sensitivity_bp in COST_RUNS:
            print(f"\n{'='*72}")
            print(f"  Cost config: headline={headline_bp}bp, sensitivity={sensitivity_bp}bp")
            print(f"  (Will read _net_20bp as {headline_bp}bp, _net_30bp as {sensitivity_bp}bp)")
            print(f"{'='*72}")
            update_cost_config(headline_bp, sensitivity_bp)
            run_main(backtest_output_dir, args.end)

            column_cost_map = [
                ("_net_20bp", headline_bp),
                ("_net_30bp", sensitivity_bp),
            ]
            for prefix, vlabel in VARIANTS:
                for suffix, logical_cost in column_cost_map:
                    col = f"{prefix}{suffix}"
                    df_check = pd.read_parquet(
                        os.path.join(backtest_output_dir, "portfolio_returns.parquet")
                    )
                    if col not in df_check.columns:
                        print(f"  [WARN] {col} not found")
                        continue
                    m = extract(col, backtest_output_dir)
                    results.append({
                        "variant": vlabel, "prefix": prefix,
                        "cost_bp": logical_cost, **m,
                    })
                    print(f"  {vlabel:<15} @ {logical_cost:3d}bp:  "
                          f"raw={m['raw_sharpe']:+.3f}  "
                          f"excess={m['excess_sharpe']:+.3f}  "
                          f"CI=[{m['bs_ci_lo']:+.3f},{m['bs_ci_hi']:+.3f}]")
    finally:
        shutil.copy(CONFIG_BACKUP, CONFIG_PATH)
        os.remove(CONFIG_BACKUP)
        print("\nConfig restored.")

    print("\n" + "=" * 72)
    print("  COST STRESS SUMMARY (4 cost levels)")
    print("=" * 72)
    df = pd.DataFrame(results).sort_values(["prefix", "cost_bp"]).reset_index(drop=True)
    for vlabel in df["variant"].unique():
        sub = df[df["variant"] == vlabel]
        print(f"\n  {vlabel}")
        print(f"  {'Cost':>6} {'Raw SR':>10} {'Excess SR':>11} {'Ann.Ret':>10} "
              f"{'Max DD':>9} {'CI Lo':>9} {'CI Hi':>9}")
        print("  " + "-" * 72)
        for _, row in sub.iterrows():
            print(f"  {row['cost_bp']:>4}bp  {row['raw_sharpe']:>+10.3f} {row['excess_sharpe']:>+11.3f} "
                  f"{row['ann_return']:>+10.2%} {row['max_dd']:>+9.2%} "
                  f"{row['bs_ci_lo']:>+9.3f} {row['bs_ci_hi']:>+9.3f}")

    out_csv = os.path.join(MY_OUTPUT, "ls_cost_stress.csv")
    df.to_csv(out_csv, index=False)
    print(f"\nSaved -> {out_csv}")
    print(
        "\nNote: pristine headline outputs in coursework_two/output/ were not "
        "touched — no canonical re-run required."
    )


if __name__ == "__main__":
    main()
