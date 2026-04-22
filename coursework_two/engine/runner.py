"""CLI entry point for the CW2 backtest engine (PLAN §7.11–7.13).

Modes (``--mode``):
    full        — single backtest from config → all Parquet artefacts
    sensitivity — CPCV γ × λ grid search → sensitivity_grid.parquet
    ablation    — run with each factor removed → ablation_results.parquet
    stress      — crisis-window diagnostics → stress_results.parquet

Usage
-----
    poetry run python -m engine.runner --mode full
    poetry run python -m engine.runner --mode sensitivity --config config/backtest_config.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from engine.backtest import BacktestEngine
from engine.config import load_config
from engine.costs import CostModel
from engine.data_loader import DataLoader
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.types import Strategy
from engine.zscore import ZScoreEngine


console = Console()


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)],
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CW2 Backtest Engine")
    p.add_argument("--config", default="config/backtest_config.yaml", help="Path to YAML config")
    p.add_argument(
        "--mode",
        choices=["full", "sensitivity", "ablation", "stress", "monte_carlo", "regime_perf"],
        default="full",
        help="Run mode (default: full). monte_carlo + regime_perf are post-backtest "
             "analytics that operate on the existing output/*.parquet files.",
    )
    p.add_argument("--start", default=None, help="Override start date (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="Override end date (YYYY-MM-DD)")
    p.add_argument(
        "--output-dir", default="output", help="Directory for Parquet output files"
    )
    p.add_argument(
        "--use-hmm",
        action="store_true",
        help="Use HMM regime classifier instead of percentile (§5.6)",
    )
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return p.parse_args()


def run_full(cfg, start, end, out_dir, use_hmm):
    dl = DataLoader(cfg)
    if not dl.health_check():
        console.print("[red]CW1 DB unhealthy — aborting[/red]")
        sys.exit(2)
    fe = FactorEngine(cfg)
    ze = ZScoreEngine(cfg)
    pe = PortfolioEngine(cfg)
    cm = CostModel(cfg)
    engine = BacktestEngine(
        cfg=cfg,
        data_loader=dl,
        factor_engine=fe,
        zscore_engine=ze,
        portfolio_engine=pe,
        cost_model=cm,
    )
    console.print(f"[bold green]Running full backtest: {start} → {end}[/bold green]")
    result = engine.run(
        start=start,
        end=end,
        strategies_to_run=(Strategy.STATIC, Strategy.DYNAMIC_GRID, Strategy.DYNAMIC_BANDIT),
        use_hmm=use_hmm,
    )
    result.save(out_dir)
    console.print(f"[bold green]✓ Saved artefacts to {out_dir}[/bold green]")
    console.print(f"  config_hash = {result.config_hash}")
    console.print(f"  data_sha256 = {result.data_snapshot_sha256[:16]}…")
    console.print(f"  git_sha     = {result.git_sha or '(none)'}")


def main():
    args = _parse_args()
    _setup_logging(args.log_level)

    cfg = load_config(args.config)
    # Resource-exhaustion guardrail (security audit finding #5)
    if cfg.backtest.n_workers in (-1, 0):
        cfg.backtest.n_workers = max(1, (os.cpu_count() or 2) - 1)
    elif cfg.backtest.n_workers > (os.cpu_count() or 1) * 2:
        cfg.backtest.n_workers = (os.cpu_count() or 2) * 2
    start = date.fromisoformat(args.start) if args.start else cfg.dates.oos_start
    end = date.fromisoformat(args.end) if args.end else cfg.dates.oos_end

    if args.mode == "full":
        run_full(cfg, start, end, args.output_dir, args.use_hmm)
    elif args.mode == "sensitivity":
        from analytics.sensitivity import run_sensitivity_cpcv

        run_sensitivity_cpcv(cfg, start, end, args.output_dir)
    elif args.mode == "ablation":
        from analytics.ablation import run_ablation

        run_ablation(cfg, start, end, args.output_dir)
    elif args.mode == "stress":
        from analytics.stress import run_stress

        run_stress(cfg, args.output_dir)
    elif args.mode == "monte_carlo":
        from analytics.monte_carlo import run_monte_carlo

        run_monte_carlo(args.output_dir)
    elif args.mode == "regime_perf":
        from analytics.regime_performance import run_regime_performance

        run_regime_performance(args.output_dir)
    else:
        raise ValueError(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    main()
