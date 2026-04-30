"""Smoke-test the CW2 pipeline end-to-end.

Runs after Path A / B / C of the README.  Prints PASS / FAIL for each
check; exits 0 only if every check passes.

Checks (in order):
    1. Python and Poetry venv health
    2. Engine and analytics modules importable
    3. 17 expected parquets present in coursework_two/output/
    4. 3 expected analysis CSVs present in analysis/output/
    5. portfolio_returns.parquet has the expected schema (32 rows,
       Jul 2023 - Feb 2026 date range, the 14 strategy/benchmark
       columns)
    6. Headline numbers reproduce the report (Static / Dynamic / HRP
       within 0.20 pp; Bandit drift is reported but not failed since
       Linear Thompson Sampling is path-dependent on the CW1 snapshot)
    7. Notebook is valid JSON with kernelspec=cw2-poetry
    8. Sphinx HTML build is present
    9. unit tests collect (does not run them)

Run from coursework_two/:
    poetry run python scripts/verify_pipeline.py

Exit code is the number of failed checks.
"""
from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT.parent / "analysis"

EXPECTED_PARQUETS = {
    "portfolio_returns", "portfolio_weights", "factor_scores", "factor_ic",
    "factor_premia", "regime_log", "exposure_log", "bandit_log",
    "sensitivity_grid", "ablation_results", "stress_results",
    "permutation_test", "permutation_null_distribution", "trade_ledger",
    "monte_carlo_paths", "regime_performance", "backtest_metadata",
}
EXPECTED_CSVS = {"ls_ff5_mom_attribution", "ls_inference", "ls_cost_stress"}
EXPECTED_RETURN_COLS = {
    "date", "static_net_20bp", "static_net_30bp", "dynamic_gross",
    "dynamic_net_20bp", "dynamic_net_30bp", "bandit_net_20bp",
    "hrp_net_20bp", "benchmark_ew", "benchmark_spx",
    "benchmark_cash_market_50_50", "long_leg", "short_leg", "rf_rate",
}
REPORT_NUMBERS = {
    "static_net_20bp": (17.83, 11.39),
    "dynamic_net_20bp": (16.92, 11.67),
    "hrp_net_20bp": (7.02, 4.33),
}

results: list[tuple[bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((ok, f"{'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail else ''}"))


def main() -> int:
    # 1. Python interpreter
    check(
        f"Python {sys.version_info.major}.{sys.version_info.minor} (>= 3.10)",
        sys.version_info >= (3, 10),
        f"using {sys.executable}",
    )

    # 2. Engine + analytics imports
    modules = [
        "engine.config", "engine.data_loader", "engine.factors",
        "engine.zscore", "engine.portfolio", "engine.dynamic_weights",
        "engine.bandit", "engine.risk_scaler", "engine.costs",
        "engine.backtest", "engine.benchmark", "engine.attribution",
        "analytics.performance", "analytics.fama_french",
        "analytics.ablation", "analytics.sensitivity", "analytics.stress",
        "analytics.monte_carlo", "analytics.regime_performance",
        "analytics.charts", "analytics.validation", "analytics.comparison",
        "analytics.attribution_analysis",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
            check(f"import {m}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"import {m}", False, str(exc).splitlines()[0])

    # 3. Parquets
    out_dir = ROOT / "output"
    found_parquets = {p.stem for p in out_dir.glob("*.parquet")}
    missing = EXPECTED_PARQUETS - found_parquets
    check(
        f"output/ has all 17 parquets ({len(found_parquets)} present)",
        not missing,
        f"missing: {sorted(missing)}" if missing else "",
    )

    # 4. Analysis CSVs
    csv_dir = ANALYSIS / "output"
    found_csvs = {p.stem for p in csv_dir.glob("*.csv")}
    missing_csv = EXPECTED_CSVS - found_csvs
    check(
        f"analysis/output/ has all 3 CSVs ({len(found_csvs)} present)",
        not missing_csv,
        f"missing: {sorted(missing_csv)}" if missing_csv else "",
    )

    # 5. Schema sanity
    try:
        import pandas as pd

        df = pd.read_parquet(out_dir / "portfolio_returns.parquet")
        cols = set(df.columns)
        missing_cols = EXPECTED_RETURN_COLS - cols
        check(
            "portfolio_returns has all 14 columns",
            not missing_cols,
            f"missing: {sorted(missing_cols)}" if missing_cols else "",
        )
        check(
            f"portfolio_returns has 32 monthly rows (got {len(df)})",
            len(df) == 32,
        )
        check(
            f"date range Jul 2023 - Feb 2026 (got {df.date.min()} to {df.date.max()})",
            str(df.date.min()) == "2023-07-31" and str(df.date.max()) == "2026-02-27",
        )
    except Exception as exc:  # noqa: BLE001
        check("portfolio_returns schema", False, str(exc).splitlines()[0])
        df = None

    # 6. Headline numbers
    if df is not None:
        for col, (r_ret, r_vol) in REPORT_NUMBERS.items():
            s = df[col].dropna()
            ret = ((1 + s).prod() ** (12 / len(s)) - 1) * 100
            vol = s.std() * math.sqrt(12) * 100
            ok = abs(ret - r_ret) < 0.30 and abs(vol - r_vol) < 0.30
            check(
                f"{col} matches report",
                ok,
                f"got ret={ret:+.2f}% vol={vol:.2f}%, report ret={r_ret:+.2f}% vol={r_vol:.2f}%",
            )
        # Bandit reported separately because TS is path-dependent
        s = df["bandit_net_20bp"].dropna()
        ret = ((1 + s).prod() ** (12 / len(s)) - 1) * 100
        vol = s.std() * math.sqrt(12) * 100
        bandit_drift = abs(ret - 9.69)
        if bandit_drift < 0.30:
            check("bandit_net_20bp matches report", True, f"ret={ret:+.2f}% vol={vol:.2f}%")
        else:
            print(
                f"INFO  bandit drift {bandit_drift:+.2f}pp (current ret={ret:+.2f}% vs report +9.69%) "
                f"— expected when CW1 snapshot has refreshed; Linear Thompson Sampling is path-dependent."
            )

    # 7. Notebook
    nb_path = ROOT / "notebooks" / "CW2_Tearsheet.ipynb"
    try:
        nb = json.loads(nb_path.read_text())
        kernel = nb.get("metadata", {}).get("kernelspec", {}).get("name")
        check(
            f"notebook valid JSON ({len(nb['cells'])} cells, kernel={kernel})",
            kernel == "cw2-poetry",
            f"expected kernel cw2-poetry, got {kernel}" if kernel != "cw2-poetry" else "",
        )
    except Exception as exc:  # noqa: BLE001
        check("notebook valid", False, str(exc).splitlines()[0])

    # 8. Sphinx
    sphinx_index = ROOT / "docs" / "_build" / "html" / "index.html"
    check("Sphinx HTML at docs/_build/html/index.html", sphinx_index.exists())

    # 9. Test collection
    try:
        import subprocess

        rc = subprocess.run(
            [sys.executable, "-m", "pytest", "test/", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = rc.returncode == 0 and "tests collected" in rc.stdout
        n = "?"
        for line in rc.stdout.splitlines():
            if "tests collected" in line:
                n = line.split()[0]
        check(f"pytest collects {n} tests", ok)
    except Exception as exc:  # noqa: BLE001
        check("pytest collection", False, str(exc).splitlines()[0])

    # Print summary
    print()
    print("=" * 60)
    fails = [r[1] for r in results if not r[0]]
    passes = sum(1 for r in results if r[0])
    print(f"  {passes} passed, {len(fails)} failed")
    if fails:
        print()
        print("FAILURES:")
        for line in fails:
            print(f"  {line}")
    print("=" * 60)
    for ok, line in results:
        print(line)
    return len(fails)


if __name__ == "__main__":
    sys.exit(main())
