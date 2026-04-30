"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Rich-based animated pipeline dashboard
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Production-grade visual progress tracking using ``rich``.  All parallel
sources share a *single* ``Progress`` + ``Live`` display, so Group A
(prices + fundamentals), Group A.5+A.6, Group D+E, and the parallel
ratios workers all render their live bars side-by-side without conflict.

Key features:
  - Single shared Live dashboard with a dynamic header panel that shows
    overall pipeline progress (ok/fail/skip totals, elapsed, rate)
  - Per-source animated bars: spinner · name · bar · % · M/N · ✓✗⊘
    counters · elapsed · ETA · throughput (tickers/s) · last ticker
  - Completion lines with colour-coded mini ratio bars (████░░)
  - Gantt-chart timeline in the summary showing stage overlap/parallelism
  - ``close()`` cleanly stops the Live before summary tables
  - ``Rule``-based parallel-group banners for clear pipeline structure
  - Pre-flight health-check table with coloured latency bars
  - Circuit-breaker, downloader stats, and post-run data verification
    tables with colour-coded rates
  - Full pipeline summary panel with sequential-vs-parallel efficiency

Falls back to plain text logging if ``rich`` is not installed.

"""

import threading
import time
from contextlib import contextmanager
from datetime import datetime

from modules.utils.info_logger import pipeline_logger

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.rule import Rule
    from rich.table import Column, Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# ── colour helpers ──────────────────────────────────────────────────────────


def _rate_markup(rate: float) -> str:
    """Return colour-coded success-rate percentage markup."""
    c = "bright_green" if rate >= 90 else "yellow" if rate >= 50 else "red"
    return f"[{c}]{rate:.0f}%[/{c}]"


def _minibar(ok: int, fail: int, skip: int, width: int = 14) -> str:
    """Return a colour-coded mini progress bar: ████░░░ style.

    Green = success, red = failed, yellow = skipped.
    """
    total = max(ok + fail + skip, 1)
    n_ok = round(ok / total * width)
    n_fail = round(fail / total * width)
    n_skip = width - n_ok - n_fail
    return (
        f"[green]{'█' * n_ok}[/green]"
        f"[red]{'█' * max(n_fail, 0)}[/red]"
        f"[yellow]{'░' * max(n_skip, 0)}[/yellow]"
    )


def _elapsed_fmt(seconds: float) -> str:
    """Format seconds as 'Hh MMm SSs' or 'MMm SSs' or 'SSs'."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _latency_bar(ms: float, max_ms: float = 500) -> str:
    """Return a short coloured latency indicator bar."""
    if ms <= 0:
        return "[dim]·[/dim]" * 5
    width = 5
    filled = round(min(ms / max_ms, 1.0) * width)
    colour = "bright_green" if ms < 50 else "yellow" if ms < 200 else "red"
    return f"[{colour}]{'▪' * filled}[/{colour}]" f"[dim]{'▫' * (width - filled)}[/dim]"


def _gantt_bar(start: float, end: float, total: float, width: int = 32) -> str:
    """Return a Gantt bar string: leading blanks + filled segment + trailing."""
    if total <= 0:
        return " " * width
    pre = round(start / total * width)
    span = max(round((end - start) / total * width), 1)
    trail = width - pre - span
    return (
        " " * pre
        + "[bold bright_cyan]"
        + "█" * span
        + "[/bold bright_cyan]"
        + "[dim]"
        + "░" * max(trail, 0)
        + "[/dim]"
    )


# ── Live header renderable ───────────────────────────────────────────────────


class _PipelineHeader:
    """Dynamic header rendered on every Live refresh tick.

    Shows overall pipeline progress: ok/fail/skip totals, elapsed time,
    active source count, and overall throughput rate.
    """

    def __init__(self, tracker: "PipelineProgressTracker") -> None:
        self._t = tracker

    def __rich_console__(self, console, options):
        t = self._t
        elapsed = time.monotonic() - t._pipeline_start

        # Aggregate outcomes across all sources (GIL protects int ops)
        total_ok = sum(v.get("success", 0) for v in t._source_outcomes.values())
        total_fail = sum(v.get("failed", 0) for v in t._source_outcomes.values())
        total_skip = sum(v.get("skipped", 0) for v in t._source_outcomes.values())
        total_proc = total_ok + total_fail + total_skip

        rate_str = f"{total_proc / elapsed:>5.1f} t/s" if elapsed > 0.5 else "    —"
        pct = total_ok / max(total_proc, 1) * 100

        elapsed_str = _elapsed_fmt(elapsed)
        n_src_active = sum(1 for v in t._source_outcomes.values() if sum(v.values()) > 0)

        grid = Table.grid(padding=(0, 2), expand=True)
        grid.add_column(ratio=4)
        grid.add_column(ratio=3)

        grid.add_row(
            f"[dim]Run:[/dim] [bold bright_white]{t.run_id}[/bold bright_white]"
            f"  [dim]Universe:[/dim] [bold bright_green]{t.total_tickers:,}[/bold bright_green]",
            f"[dim]⏱[/dim] [bold cyan]{elapsed_str}[/bold cyan]"
            f"   [dim]⚡[/dim] [bold bright_white]{rate_str}[/bold bright_white]",
        )
        grid.add_row(
            f"[green]✓ {total_ok:,}[/green]  [red]✗ {total_fail}[/red]  [yellow]⊘ {total_skip}[/yellow]"
            f"  [dim]{total_proc:,}/{t.total_tickers:,}[/dim]",
            f"[dim]sources:[/dim] [bold]{n_src_active}[/bold]"
            f"   [bold bright_green]{pct:.1f}%[/bold bright_green] [dim]success[/dim]",
        )

        yield Panel(
            grid,
            border_style="dim bright_cyan",
            padding=(0, 2),
            expand=True,
        )


# ── Main tracker class ───────────────────────────────────────────────────────


class PipelineProgressTracker:
    """Animated pipeline dashboard.

    All sources share one ``Progress`` display inside one ``Live`` context,
    so parallel sources appear as stacked live-updating bars beneath a
    dynamic header panel showing overall pipeline stats.

    Call ``close()`` after all sources finish to stop the live display cleanly
    before printing summary tables.

    :param run_id: Unique pipeline run identifier.
    :type run_id: str
    :param total_tickers: Universe size (for banner display).
    :type total_tickers: int
    """

    def __init__(self, run_id: str, total_tickers: int = 0):
        self.run_id = run_id
        self.total_tickers = total_tickers
        self._pipeline_start: float = time.monotonic()
        self._started_at: datetime = datetime.now()
        self._source_outcomes: dict = {}
        self._source_starts: dict = {}  # source → seconds from pipeline start
        self._source_ends: dict = {}  # source → seconds from pipeline start

        # ── Rich objects ────────────────────────────────────────────────────
        if RICH_AVAILABLE:
            self._console = Console(highlight=False)
            self._progress = Progress(
                SpinnerColumn("dots2", style="bright_cyan", speed=1.2),
                TextColumn(
                    "[bold bright_white]{task.description:<22}[/bold bright_white]",
                    table_column=Column(no_wrap=True),
                ),
                BarColumn(
                    bar_width=None,
                    complete_style="bright_green",
                    finished_style="dim green",
                    pulse_style="bright_cyan",
                ),
                TaskProgressColumn(
                    text_format="[bold cyan]{task.percentage:>3.0f}%[/bold cyan]",
                    text_format_no_percentage="[dim] ?  [/dim]",
                ),
                TextColumn("[dim]{task.completed:>4}/{task.total:<4}[/dim]"),
                TextColumn(
                    "[green]✓[/green][bold green]{task.fields[ok]:>4}[/bold green]"
                    "  [red]✗[/red][bold red]{task.fields[fail]:>3}[/bold red]"
                    "  [yellow]⊘[/yellow][yellow]{task.fields[skip]:>3}[/yellow]",
                    table_column=Column(no_wrap=True),
                ),
                TimeElapsedColumn(),
                TextColumn("[dim]eta[/dim]"),
                TimeRemainingColumn(),
                TextColumn(
                    "[bold]{task.fields[rate]:>8}[/bold]",
                    table_column=Column(no_wrap=True),
                ),
                TextColumn(
                    "  [dim italic]{task.fields[current]}[/dim italic]",
                    table_column=Column(no_wrap=True),
                ),
                console=self._console,
                expand=True,
                transient=False,
            )
        else:
            self._console = None
            self._progress = None

        # Single shared Live display — started on first source_progress() call
        self._live_lock: threading.Lock = threading.Lock()
        self._live: "Live | None" = None

    # ── Live display lifecycle ───────────────────────────────────────────────

    def _start_live(self) -> None:
        """Start the shared Live display (thread-safe, idempotent).

        The display is a ``Group`` of two layers:
          1. ``_PipelineHeader`` — dynamic overall-stats panel
          2. ``Progress``        — per-source animated bars
        """
        with self._live_lock:
            if self._live is None and RICH_AVAILABLE:
                display = Group(_PipelineHeader(self), self._progress)
                self._live = Live(
                    display,
                    console=self._console,
                    refresh_per_second=12,
                    transient=False,
                    vertical_overflow="visible",
                )
                self._live.start(refresh=True)

    def close(self) -> None:
        """Stop the shared Live display cleanly.

        Call this after all ``source_progress()`` contexts have exited and
        before printing circuit-breaker / summary tables.
        """
        with self._live_lock:
            if self._live is not None:
                self._live.stop()
                self._live = None

    # ── Banner ───────────────────────────────────────────────────────────────

    def print_banner(self) -> None:
        """Print the pipeline startup banner with parallelism config."""
        if not RICH_AVAILABLE:
            pipeline_logger.info("=" * 68)
            pipeline_logger.info("  Kolmogorov's team — Systematic Equity Pipeline")
            pipeline_logger.info(f"  Run ID   : {self.run_id}")
            pipeline_logger.info(f"  Universe : {self.total_tickers:,} tickers")
            pipeline_logger.info(f"  Started  : {self._started_at.strftime('%Y-%m-%d %H:%M:%S')}")
            pipeline_logger.info("=" * 68)
            return

        # ── Metadata grid ────────────────────────────────────────────────────
        meta = Table.grid(padding=(0, 3))
        meta.add_column(style="dim", min_width=14)
        meta.add_column(min_width=40)
        meta.add_row("Run ID", f"[bold white]{self.run_id}[/bold white]")
        meta.add_row(
            "Universe",
            f"[bold bright_green]{self.total_tickers:,}[/bold bright_green]" " [dim]equity tickers[/dim]",
        )
        meta.add_row("Started", f"[dim]{self._started_at.strftime('%Y-%m-%d  %H:%M:%S')} UTC[/dim]")
        meta.add_row(
            "Sources",
            "[dim]prices · fundamentals · EDGAR · Finnhub · FX · VIX"
            " · RFR · benchmark · ratios · ESG · sentiment[/dim]",
        )

        # ── Parallelism config table ─────────────────────────────────────────
        pcfg = Table(
            show_header=True,
            header_style="bold white on dark_blue",
            border_style="dim blue",
            show_lines=False,
            box=None,
            padding=(0, 3),
        )
        pcfg.add_column("Group", style="bold yellow", min_width=14)
        pcfg.add_column("Sources", style="dim cyan", min_width=36)
        pcfg.add_column("Workers", justify="center", min_width=8)
        rows = [
            ("A", "prices + fundamentals  [dim](source-parallel threads)[/dim]", "4-6 / batch"),
            ("A.5+6", "EDGAR (US) + Finnhub (non-US)  [dim](ticker-parallel pool)[/dim]", "6 + 3"),
            ("B.1", "FX + risk-free rate  [dim](source-parallel threads)[/dim]", "2"),
            ("B.2-3", "VIX + benchmark  [dim](sequential — yf.download)[/dim]", "1"),
            ("C", "company ratios  [dim](ticker-parallel pool)[/dim]", "8"),
            ("D+E", "ESG (LSEG batch) + sentiment  [dim](ticker-parallel pool)[/dim]", "1+6"),
        ]
        for grp, srcs, wkrs in rows:
            pcfg.add_row(grp, srcs, f"[bold bright_cyan]{wkrs}[/bold bright_cyan]")

        # ── Combine into panel ───────────────────────────────────────────────
        inner = Table.grid(padding=(1, 0))
        inner.add_row(meta)
        inner.add_row("")
        inner.add_row("[dim]─ Parallelism Configuration ─────────────────────────────────────────[/dim]")
        inner.add_row(pcfg)

        self._console.print()
        self._console.print(
            Panel(
                inner,
                title="[bold bright_white]Kolmogorov's team[/bold bright_white]",
                subtitle="[dim]Systematic Equity Pipeline[/dim]",
                border_style="bright_cyan",
                padding=(1, 4),
                expand=False,
            )
        )
        self._console.print()

    # ── Per-source progress bar ──────────────────────────────────────────────

    @contextmanager
    def source_progress(self, source: str, total: int):
        """Live progress bar for one data source.

        All sources share the same ``Progress`` / ``Live`` instance, so
        parallel sources (e.g. prices + fundamentals) render as stacked
        bars beneath the dynamic header panel.

        Yields an ``update(symbol, status)`` callback where *status* is
        ``'SUCCESS'``, ``'FAILED'``, or ``'SKIPPED'``.

        :param source: Source name (prices, fundamentals, esg, …).
        :type source: str
        :param total: Total items to process.
        :type total: int
        """
        outcomes = {"success": 0, "failed": 0, "skipped": 0}
        self._source_outcomes[source] = outcomes
        source_start_mono = time.monotonic()
        self._source_starts[source] = source_start_mono - self._pipeline_start

        if not RICH_AVAILABLE or total == 0:

            def _plain(symbol: str, status: str) -> None:
                k = status.lower()
                if k in outcomes:
                    outcomes[k] += 1

            yield _plain
            self._source_ends[source] = time.monotonic() - self._pipeline_start
            return

        source_upper = source.upper()

        # Add this source as a new task row in the shared Progress
        task_id = self._progress.add_task(
            source_upper,
            total=total,
            current="—",
            ok=0,
            fail=0,
            skip=0,
            rate="   --/s",
        )

        # Lazily start the shared Live display (only the first thread does it)
        self._start_live()

        def update(symbol: str, status: str) -> None:
            k = status.lower()
            if k in outcomes:
                outcomes[k] += 1

            elapsed = time.monotonic() - source_start_mono
            processed = outcomes["success"] + outcomes["failed"] + outcomes["skipped"]
            rate_str = f"{processed / elapsed:>5.1f}/s" if elapsed > 0.5 else "   --/s"

            if status == "SUCCESS":
                current_str = f"[green]↓ {symbol}[/green]"
            elif status == "FAILED":
                current_str = f"[red]✗ {symbol}[/red]"
            else:
                current_str = f"[yellow]⊘ {symbol}[/yellow]"

            self._progress.update(
                task_id,
                advance=1,
                current=current_str,
                ok=outcomes["success"],
                fail=outcomes["failed"],
                skip=outcomes["skipped"],
                rate=rate_str,
            )

        yield update

        # Record end time for Gantt chart
        self._source_ends[source] = time.monotonic() - self._pipeline_start

        # Ensure the bar reaches 100 %
        self._progress.update(task_id, completed=total, current="[dim]done[/dim]")

        # ── Completion summary line ──────────────────────────────────────────
        elapsed = time.monotonic() - source_start_mono
        ok = outcomes["success"]
        fail = outcomes["failed"]
        skip = outcomes["skipped"]
        rate = (ok / total * 100) if total > 0 else 0.0
        tps = f"{total / elapsed:.1f}/s" if elapsed > 0.5 else "--"
        mini = _minibar(ok, fail, skip)

        icon = "✅" if fail == 0 else ("⚠ " if ok >= fail else "❌")
        rc = _rate_markup(rate)
        self._console.print(
            f"  {icon}  "
            f"[bold bright_white]{source_upper:<24}[/bold bright_white]"
            f"{mini}  "
            f"[green]✓ {ok:>4}[/green]  "
            f"[red]✗ {fail:>3}[/red]  "
            f"[yellow]⊘ {skip:>3}[/yellow]  "
            f"{rc}  "
            f"[dim]{_elapsed_fmt(elapsed)}  ·  {tps}[/dim]"
        )

    # ── Phase markers ────────────────────────────────────────────────────────

    def print_phase_start(self, source: str) -> None:
        """Print a phase-start indicator with timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        if not RICH_AVAILABLE:
            pipeline_logger.info(f"▶ {source.upper()} — starting  [{ts}]")
            return
        self._console.print(
            f"  [bold bright_cyan]▶[/bold bright_cyan]  "
            f"[bold]{source.upper()}[/bold]"
            f"  [dim]initialising…[/dim]"
            f"  [dim italic]{ts}[/dim italic]"
        )

    def print_phase_complete(self, source: str, elapsed: float, rows: int) -> None:
        """Print a phase-complete indicator with throughput and mini bar.

        :param source: Source name.
        :type source: str
        :param elapsed: Phase wall-clock time in seconds.
        :type elapsed: float
        :param rows: Database rows written.
        :type rows: int
        """
        outcomes = self._source_outcomes.get(source, {})
        ok = outcomes.get("success", 0)
        fail = outcomes.get("failed", 0)
        skip = outcomes.get("skipped", 0)
        mini = _minibar(ok, fail, skip, width=10)
        throughput = f"  [dim]({rows / elapsed:.0f} rows/s)[/dim]" if elapsed > 0 and rows > 0 else ""
        if not RICH_AVAILABLE:
            msg = f"◀ {source.upper()} complete: {rows:,} rows  " f"{_elapsed_fmt(elapsed)}"
            if throughput:
                msg += f"  ({rows / elapsed:.0f} rows/s)"
            pipeline_logger.info(msg)
            return
        self._console.print(
            f"  [bold bright_green]◀[/bold bright_green]  "
            f"[bold]{source.upper()}[/bold]"
            f"  {mini}"
            f"  [bold bright_green]{rows:,}[/bold bright_green] [dim]rows[/dim]"
            f"  [dim]{_elapsed_fmt(elapsed)}[/dim]"
            f"{throughput}"
        )

    def print_parallel_group_start(self, group_name: str, sources: list, n_threads: int) -> None:
        """Print a styled banner before a parallel group launches.

        :param group_name: Label for the group.
        :type group_name: str
        :param sources: Source names that will run concurrently.
        :type sources: list
        :param n_threads: Number of concurrent threads / workers.
        :type n_threads: int
        """
        if not RICH_AVAILABLE:
            pipeline_logger.info(
                f"[PARALLEL] {group_name}: "
                f"{' + '.join(s.upper() for s in sources)} "
                f"({n_threads} thread{'s' if n_threads != 1 else ''})"
            )
            return

        ts = datetime.now().strftime("%H:%M:%S")
        tl = f"{n_threads} worker{'s' if n_threads != 1 else ''}"
        src = "  [dim]⊕[/dim]  ".join(f"[bold bright_cyan]{s.upper()}[/bold bright_cyan]" for s in sources)
        self._console.print(
            Rule(
                f"[bold yellow]⚡ PARALLEL[/bold yellow]  "
                f"[dim]{group_name}[/dim]   {src}"
                f"   [dim]({tl} · {ts})[/dim]",
                style="dim yellow",
                align="center",
            )
        )

    # ── Health checks ────────────────────────────────────────────────────────

    def print_health_checks(self, results: list) -> None:
        """Display pre-flight health-check results with latency bars.

        :param results: List of ``HealthCheckResult`` instances.
        :type results: list
        """
        if not RICH_AVAILABLE:
            pipeline_logger.info("─── Pre-flight Health Checks ───")
            for r in results:
                tag = "PASS" if r.healthy else "FAIL"
                pipeline_logger.info(f"  [{tag}]  {r.name:<20}  {r.latency_ms:>6.0f}ms  {r.message}")
            return

        table = Table(
            title="[bold]Pre-flight Health Checks[/bold]",
            show_header=True,
            header_style="bold white on dark_green",
            border_style="dim green",
            show_lines=False,
            pad_edge=True,
        )
        table.add_column("", justify="center", width=3)  # led
        table.add_column("Service", style="bold cyan", min_width=18)
        table.add_column("Status", justify="center", min_width=6)
        table.add_column("Latency", justify="right", min_width=9)
        table.add_column("Bar", justify="left", min_width=7)
        table.add_column("Details", style="dim", min_width=38)

        all_pass = True
        max_lat = max((r.latency_ms for r in results), default=500) or 500
        for r in results:
            if r.healthy:
                led = "[bright_green]●[/bright_green]"
                status = "[bold bright_green]PASS[/bold bright_green]"
                lat_c = "bright_green" if r.latency_ms < 100 else "yellow"
            else:
                led = "[red]●[/red]"
                status = "[bold red]FAIL[/bold red]"
                lat_c = "red"
                all_pass = False
            lat_bar = _latency_bar(r.latency_ms, max_ms=max(max_lat, 200))
            table.add_row(
                led,
                r.name,
                status,
                f"[{lat_c}]{r.latency_ms:.0f}[/{lat_c}] ms",
                lat_bar,
                r.message[:55],
            )

        self._console.print()
        self._console.print(table)
        if all_pass:
            self._console.print(
                "  [bold bright_green]"
                "✅  All checks passed — pipeline cleared for launch"
                "[/bold bright_green]"
            )
        else:
            self._console.print("  [bold red]❌  Some checks failed — review above[/bold red]")
        self._console.print()

    # ── Circuit breakers ─────────────────────────────────────────────────────

    def print_circuit_breaker_status(self, breakers: list) -> None:
        """Display circuit-breaker states with recovery timing.

        :param breakers: List of ``CircuitBreaker`` instances.
        :type breakers: list
        """
        if not RICH_AVAILABLE:
            for cb in breakers:
                d = cb.to_dict()
                pipeline_logger.info(
                    f"  CB [{d['name']}]: {d['state']}  "
                    f"failures={d['failure_count']}  trips={d['total_trips']}"
                )
            return

        table = Table(
            title="[bold]Circuit Breaker Status[/bold]",
            show_header=True,
            header_style="bold white on dark_blue",
            border_style="dim blue",
            show_lines=False,
        )
        table.add_column("Source", style="bold cyan", min_width=20)
        table.add_column("State", justify="center", min_width=12)
        table.add_column("Failures", justify="right", style="dim", min_width=9)
        table.add_column("Threshold", justify="right", style="dim", min_width=9)
        table.add_column("Total Trips", justify="right", min_width=11)
        table.add_column("Recovery (s)", justify="right", style="dim", min_width=12)

        for cb in breakers:
            d = cb.to_dict()
            state = d["state"]
            if state == "CLOSED":
                st = "[bold bright_green]● CLOSED[/bold bright_green]"
                trip_col = "dim"
            elif state == "OPEN":
                st = "[bold red]● OPEN[/bold red]"
                trip_col = "red"
            else:
                st = "[bold yellow]● HALF-OPEN[/bold yellow]"
                trip_col = "yellow"

            trips = d["total_trips"]
            trip_str = f"[{trip_col}]{trips}[/{trip_col}]" if trips > 0 else "[dim]0[/dim]"
            table.add_row(
                d["name"],
                st,
                str(d["failure_count"]),
                str(d["failure_threshold"]),
                trip_str,
                str(d.get("recovery_timeout", "—")),
            )

        self._console.print()
        self._console.print(table)

    # ── Downloader stats ─────────────────────────────────────────────────────

    def print_downloader_stats(self, downloaders: list) -> None:
        """Display per-downloader success statistics with wait times.

        :param downloaders: List of ``BaseDownloader`` instances.
        :type downloaders: list
        """
        if not RICH_AVAILABLE:
            for dl in downloaders:
                s = dl.stats
                pipeline_logger.info(
                    f"  [{s['source']}]  downloads={s['downloads']}  "
                    f"success_rate={s['success_rate']:.0f}%"
                )
            return

        table = Table(
            title="[bold]Downloader Statistics[/bold]",
            show_header=True,
            header_style="bold white on dark_magenta",
            border_style="dim magenta",
            show_lines=False,
        )
        table.add_column("Source", style="bold cyan", min_width=22)
        table.add_column("Downloads", justify="right", min_width=9)
        table.add_column("Successes", justify="right", style="green", min_width=9)
        table.add_column("Failures", justify="right", style="red", min_width=8)
        table.add_column("Success %", justify="right", min_width=10)
        table.add_column("Rate Waits", justify="right", style="dim", min_width=10)
        table.add_column("Total Wait", justify="right", style="dim", min_width=10)
        table.add_column("Avg Wait", justify="right", style="dim", min_width=9)

        for dl in downloaders:
            s = dl.stats
            r = s["success_rate"]
            rs = _rate_markup(r)
            rl = s.get("rate_limiter", {})
            w = rl.get("total_waits", 0)
            wt = rl.get("total_wait_time", 0.0)
            avg = f"{wt / w:.2f}s" if w > 0 else "—"
            table.add_row(
                s["source"],
                str(s["downloads"]),
                str(s["successes"]),
                str(s["failures"]),
                rs,
                str(w),
                f"{wt:.1f}s",
                avg,
            )

        self._console.print()
        self._console.print(table)

    # ── Pipeline summary ─────────────────────────────────────────────────────

    def print_summary(self, metrics_dict: dict) -> None:
        """Print the final pipeline run summary table and Gantt timeline.

        :param metrics_dict: ``PipelineMetrics.to_dict()`` output.
        :type metrics_dict: dict
        """
        if not RICH_AVAILABLE:
            pipeline_logger.info("─── Pipeline Run Summary ───")
            for src, data in metrics_dict.get("sources", {}).items():
                ok = data.get("success", 0)
                fail = data.get("failed", 0)
                skip = data.get("skipped", 0)
                tot = ok + fail + skip
                rate = (ok / tot * 100) if tot > 0 else 0
                pipeline_logger.info(
                    f"  {src:<24}  {data.get('total_rows', 0):>8,} rows  "
                    f"ok={ok}  fail={fail}  skip={skip}  "
                    f"rate={rate:.0f}%  {data.get('elapsed_seconds', 0):.1f}s"
                )
            pipeline_logger.info(f"  TOTAL  {metrics_dict.get('total_elapsed_seconds', 0):.1f}s")
            return

        total_elapsed = metrics_dict.get("total_elapsed_seconds", 0)
        sources = metrics_dict.get("sources", {})

        self._console.print()
        self._console.print(
            Rule(
                "[bold bright_white]  Pipeline Complete  [/bold bright_white]",
                style="bright_cyan",
            )
        )

        # ── Source breakdown table ──────────────────────────────────────────
        table = Table(
            title="[bold]Pipeline Run Summary[/bold]",
            show_header=True,
            header_style="bold white on dark_blue",
            border_style="dim blue",
            show_lines=True,
            expand=False,
        )
        table.add_column("Source", style="bold cyan", min_width=22)
        table.add_column("Time (s)", justify="right", style="dim", min_width=8)
        table.add_column("Rows", justify="right", style="bold green", min_width=10)
        table.add_column("t/s", justify="right", style="dim", min_width=6)
        table.add_column("OK", justify="right", style="bright_green", min_width=6)
        table.add_column("Fail", justify="right", style="red", min_width=6)
        table.add_column("Skip", justify="right", style="yellow", min_width=6)
        table.add_column("Ratio", justify="left", min_width=16)
        table.add_column("Rate", justify="right", min_width=6)

        total_rows = 0
        seq_equiv = 0.0  # sum of all stage times (sequential baseline)
        for src, data in sources.items():
            elapsed = data.get("elapsed_seconds", 0)
            rows = data.get("total_rows", 0)
            ok = data.get("success", 0)
            fail = data.get("failed", 0)
            skip = data.get("skipped", 0)
            total_t = ok + fail + skip
            rate = (ok / total_t * 100) if total_t > 0 else 0.0
            tps = f"{total_t / elapsed:.1f}" if elapsed > 0 and total_t > 0 else "—"
            total_rows += rows
            seq_equiv += elapsed
            mini = _minibar(ok, fail, skip, width=14)

            table.add_row(
                src.upper(),
                f"{elapsed:.1f}",
                f"{rows:,}",
                tps,
                str(ok),
                str(fail),
                str(skip),
                mini,
                _rate_markup(rate),
            )

        table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold]{total_elapsed:.1f}[/bold]",
            f"[bold bright_green]{total_rows:,}[/bold bright_green]",
            "—",
            "",
            "",
            "",
            "",
            style="bold",
        )
        self._console.print(table)

        # ── Gantt timeline ───────────────────────────────────────────────────
        gantt_total = total_elapsed if total_elapsed > 0 else 1.0

        gtable = Table(
            title="[bold]Stage Timeline  (pipeline wall time)[/bold]",
            show_header=True,
            header_style="bold white on dark_blue",
            border_style="dim blue",
            show_lines=False,
            expand=True,
        )
        gtable.add_column("Source", style="bold cyan", min_width=22)
        gtable.add_column("Start", justify="right", style="dim", min_width=7)
        gtable.add_column("End", justify="right", style="dim", min_width=7)
        gtable.add_column("Duration", justify="right", style="dim", min_width=9)
        gtable.add_column(
            f"Timeline  (0 → {gantt_total:.0f}s)",
            min_width=36,
        )

        for src in sources:
            start_s = self._source_starts.get(src, 0.0)
            end_s = self._source_ends.get(src, start_s)
            dur = end_s - start_s
            bar = _gantt_bar(start_s, end_s, gantt_total, width=34)
            gtable.add_row(
                src.upper(),
                f"{start_s:.1f}s",
                f"{end_s:.1f}s",
                f"{dur:.1f}s",
                bar,
            )

        self._console.print()
        self._console.print(gtable)

        # ── Efficiency footer ────────────────────────────────────────────────
        speedup = (seq_equiv / total_elapsed) if total_elapsed > 0 else 1.0
        m_total, s_total = divmod(int(total_elapsed), 60)
        h_total, m_total = divmod(m_total, 60)
        wall_str = f"{h_total}h {m_total:02d}m {s_total:02d}s" if h_total else f"{m_total}m {s_total:02d}s"
        self._console.print(
            f"\n"
            f"  [dim]Run ID:[/dim]    [bold white]{metrics_dict.get('run_id', '—')}[/bold white]\n"
            f"  [dim]Wall time:[/dim] [bold cyan]{wall_str}[/bold cyan]"
            f"   [dim]Sequential equiv:[/dim] [bold]{seq_equiv:.0f}s[/bold]"
            f"   [dim]Parallelism speedup:[/dim]"
            f" [bold bright_green]{speedup:.1f}×[/bold bright_green]\n"
            f"  [dim]Tickers:[/dim]   [bold green]{self.total_tickers:,}[/bold green]"
            f"   [dim]DB rows:[/dim] [bold bright_green]{total_rows:,}[/bold bright_green]\n"
        )

    # ── Data verification ────────────────────────────────────────────────────

    def print_data_verification(self, db_client) -> None:
        """Query the database and display a comprehensive data verification table.

        Dynamically queries all pipeline tables — nothing hardcoded.

        :param db_client: Database client with ``read_query()`` method.
        """
        pipeline_logger.info("Running post-pipeline data verification...")

        queries = {
            "daily_prices": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(close_price)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(adj_close_price)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(volume)::numeric / NULLIF(COUNT(*),0) * 100, 1) "
                    "FROM systematic_equity.daily_prices"
                ),
                "fields": "close:{4}%  adj:{5}%  vol:{6}%",
            },
            "fundamentals": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "MIN(report_date)::text, MAX(report_date)::text, "
                    "ROUND(COUNT(field_value)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "COUNT(DISTINCT field_name), "
                    "COUNT(DISTINCT period_type) "
                    "FROM systematic_equity.fundamentals"
                ),
                "fields": "non-null:{4}%  fields:{5}  periods:{6}",
            },
            "fx_rates": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT currency_pair), "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(close_rate)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "0, 0 "
                    "FROM systematic_equity.fx_rates"
                ),
                "fields": "close_rate:{4}%",
            },
            "vix_data": {
                "summary": (
                    "SELECT COUNT(*), 1, "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(close_price)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(adj_close_price)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(volume)::numeric / NULLIF(COUNT(*),0) * 100, 1) "
                    "FROM systematic_equity.vix_data"
                ),
                "fields": "close:{4}%  adj:{5}%  vol:{6}%",
            },
            "risk_free_rate": {
                "summary": (
                    "SELECT COUNT(*), 1, "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(rate_pct)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "0, 0 "
                    "FROM systematic_equity.risk_free_rate"
                ),
                "fields": "rate_pct:{4}%",
            },
            "benchmark_index": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(close_price)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(adj_close_price)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(volume)::numeric / NULLIF(COUNT(*),0) * 100, 1) "
                    "FROM systematic_equity.benchmark_index"
                ),
                "fields": "close:{4}%  adj:{5}%  vol:{6}%",
            },
            "company_ratios": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "MIN(snapshot_date)::text, MAX(snapshot_date)::text, "
                    "ROUND(COUNT(field_value)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "COUNT(DISTINCT field_name), 0 "
                    "FROM systematic_equity.company_ratios"
                ),
                "fields": "non-null:{4}%  fields:{5}",
            },
            "esg_scores": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(total_esg)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(environment_score)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(governance_score)::numeric / NULLIF(COUNT(*),0) * 100, 1) "
                    "FROM systematic_equity.esg_scores"
                ),
                "fields": "total:{4}%  env:{5}%  gov:{6}%",
            },
            "news_sentiment": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "MIN(cob_date)::text, MAX(cob_date)::text, "
                    "ROUND(COUNT(avg_sentiment)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(COUNT(article_count)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                    "ROUND(AVG(article_count)::numeric, 1) "
                    "FROM systematic_equity.news_sentiment"
                ),
                "fields": "score:{4}%  articles:{5}%  avg_arts:{6}",
            },
            "company_static": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT symbol), "
                    "NULL::text, NULL::text, "
                    "COUNT(DISTINCT country), "
                    "COUNT(DISTINCT gics_sector), "
                    "COUNT(DISTINCT region) "
                    "FROM systematic_equity.company_static"
                ),
                "fields": "{4} countries  {5} sectors  {6} regions",
            },
            "ingestion_log": {
                "summary": (
                    "SELECT COUNT(*), COUNT(DISTINCT data_source), "
                    "MIN(run_timestamp)::text, MAX(run_timestamp)::text, "
                    "COUNT(DISTINCT run_id), "
                    "ROUND(SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END)::numeric "
                    "/ NULLIF(COUNT(*),0) * 100, 1), "
                    "SUM(rows_affected) "
                    "FROM systematic_equity.ingestion_log"
                ),
                "fields": "{4} runs  success:{5}%  total_rows:{6}",
            },
        }

        table_stats = []
        for table_name, q in queries.items():
            try:
                rows = db_client.read_query(q["summary"])
                if rows and rows[0]:
                    r = rows[0]
                    table_stats.append(
                        {
                            "table": table_name,
                            "rows": r[0] or 0,
                            "entities": r[1] or 0,
                            "min_date": r[2] or "—",
                            "max_date": r[3] or "—",
                            "completeness": q["fields"].format(
                                *([0] * 4 + [r[i] if r[i] is not None else 0 for i in range(4, len(r))])
                            ),
                        }
                    )
            except Exception as exc:
                pipeline_logger.warning(f"Verification query failed for {table_name}: {exc}")
                table_stats.append(
                    {
                        "table": table_name,
                        "rows": "?",
                        "entities": "?",
                        "min_date": "?",
                        "max_date": "?",
                        "completeness": f"query error: {exc}",
                    }
                )

        # ── Fundamentals field-level breakdown ──────────────────────────────
        fund_fields = []
        try:
            fund_rows = db_client.read_query(
                "SELECT field_name, COUNT(*), COUNT(field_value), "
                "ROUND(COUNT(field_value)::numeric / NULLIF(COUNT(*),0) * 100, 1), "
                "MIN(report_date)::text, MAX(report_date)::text "
                "FROM systematic_equity.fundamentals "
                "GROUP BY field_name ORDER BY field_name"
            )
            for r in fund_rows:
                fund_fields.append(
                    {
                        "field": r[0],
                        "total": r[1],
                        "non_null": r[2],
                        "pct": r[3] or 0,
                        "min_date": r[4] or "—",
                        "max_date": r[5] or "—",
                    }
                )
        except Exception as exc:
            pipeline_logger.warning(f"Fundamentals field query failed: {exc}")

        # ── Company ratios field-level breakdown ─────────────────────────────
        ratio_fields = []
        try:
            ratio_rows = db_client.read_query(
                "SELECT field_name, COUNT(*), COUNT(DISTINCT symbol), "
                "ROUND(COUNT(DISTINCT symbol)::numeric / "
                "NULLIF((SELECT COUNT(DISTINCT symbol) "
                "FROM systematic_equity.company_ratios), 0) * 100, 1), "
                "MIN(snapshot_date)::text, MAX(snapshot_date)::text "
                "FROM systematic_equity.company_ratios "
                "GROUP BY field_name ORDER BY field_name"
            )
            for r in ratio_rows:
                ratio_fields.append(
                    {
                        "field": r[0],
                        "total": r[1],
                        "symbols": r[2],
                        "pct": r[3] or 0,
                        "min_date": r[4] or "—",
                        "max_date": r[5] or "—",
                    }
                )
        except Exception as exc:
            pipeline_logger.warning(f"Ratios field query failed: {exc}")

        # ── Plain-text fallback ──────────────────────────────────────────────
        if not RICH_AVAILABLE:
            pipeline_logger.info("=" * 70)
            pipeline_logger.info("DATA VERIFICATION SUMMARY")
            pipeline_logger.info("=" * 70)
            for s in table_stats:
                dr = (
                    "—"
                    if s["min_date"] in ("—", "?") and s["max_date"] in ("—", "?")
                    else f"{s['min_date']} → {s['max_date']}"
                )
                pipeline_logger.info(
                    f"  {s['table']:20s}  rows={s['rows']:>10}  "
                    f"entities={s['entities']:>5}  {dr}  {s['completeness']}"
                )
            if fund_fields:
                pipeline_logger.info("-" * 70)
                for f in fund_fields:
                    pipeline_logger.info(
                        f"    {f['field']:25s}  total={f['total']:>6}  "
                        f"non-null={f['non_null']:>6} ({f['pct']}%)  "
                        f"{f['min_date']} → {f['max_date']}"
                    )
            if ratio_fields:
                pipeline_logger.info("-" * 70)
                pipeline_logger.info("Company Ratios Field Breakdown:")
                for f in ratio_fields:
                    pipeline_logger.info(
                        f"    {f['field']:25s}  records={f['total']:>6}  "
                        f"symbols={f['symbols']:>4} ({f['pct']}%)"
                    )
            pipeline_logger.info("=" * 70)
            return

        # ── Rich verification table ──────────────────────────────────────────
        verify_console = Console(width=max(self._console.width, 120), highlight=False)

        verify_console.print()
        verify_console.print(
            Rule(
                "[bold bright_white]  Post-Pipeline Data Verification  [/bold bright_white]",
                style="bright_green",
            )
        )

        vtable = Table(
            show_header=True,
            header_style="bold white on dark_green",
            border_style="dim green",
            show_lines=True,
            expand=True,
        )
        vtable.add_column("Table", style="bold cyan", min_width=16)
        vtable.add_column("Rows", justify="right", style="bold bright_green", min_width=10)
        vtable.add_column("Entities", justify="right", style="dim", min_width=8)
        vtable.add_column("Date Range", style="dim", min_width=26)
        vtable.add_column("Field Completeness", min_width=30, no_wrap=False, overflow="fold")

        total_rows_db = 0
        for s in table_stats:
            rv = s["rows"]
            if isinstance(rv, int):
                total_rows_db += rv
                rs = f"{rv:,}"
            else:
                rs = str(rv)
            md, xd = s["min_date"], s["max_date"]
            dr = "—" if md in ("—", "?") and xd in ("—", "?") else f"{md} → {xd}"
            vtable.add_row(s["table"], rs, str(s["entities"]), dr, s["completeness"])

        vtable.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold bright_green]{total_rows_db:,}[/bold bright_green]",
            "",
            "",
            "",
            style="bold",
        )
        verify_console.print(vtable)

        # ── Rich fundamentals breakdown ──────────────────────────────────────
        if fund_fields:
            ftable = Table(
                title="[bold]Fundamentals Field Breakdown[/bold]",
                show_header=True,
                header_style="bold white on dark_magenta",
                border_style="dim magenta",
                show_lines=True,
                expand=False,
            )
            ftable.add_column("Field", style="cyan", min_width=22)
            ftable.add_column("Total", justify="right")
            ftable.add_column("Non-NULL", justify="right", style="green")
            ftable.add_column("Bar", justify="left", min_width=10)
            ftable.add_column("Complete", justify="right", min_width=8)
            ftable.add_column("Date Range", style="dim", min_width=24)

            for f in fund_fields:
                pct = f["pct"]
                n_fill = round(pct / 100 * 10)
                bar = f"[bright_green]{'█' * n_fill}[/bright_green]" f"[dim]{'░' * (10 - n_fill)}[/dim]"
                if pct >= 80:
                    ps = f"[bright_green]{pct}%[/bright_green]"
                elif pct >= 40:
                    ps = f"[yellow]{pct}%[/yellow]"
                else:
                    ps = f"[red]{pct}%[/red]"
                ftable.add_row(
                    f["field"],
                    f"{f['total']:,}",
                    f"{f['non_null']:,}",
                    bar,
                    ps,
                    f"{f['min_date']} → {f['max_date']}",
                )

            verify_console.print()
            verify_console.print(ftable)

        # ── Rich company ratios breakdown ─────────────────────────────────────
        if ratio_fields:
            rtable = Table(
                title="[bold]Company Ratios Field Breakdown[/bold]",
                show_header=True,
                header_style="bold white on dark_blue",
                border_style="dim blue",
                show_lines=True,
                expand=False,
            )
            rtable.add_column("Field", style="cyan", min_width=22)
            rtable.add_column("Records", justify="right")
            rtable.add_column("Symbols", justify="right", style="green")
            rtable.add_column("Bar", justify="left", min_width=10)
            rtable.add_column("Coverage", justify="right", min_width=8)

            for f in ratio_fields:
                pct = f["pct"]
                n_fill = round(pct / 100 * 10)
                bar = f"[bright_green]{'█' * n_fill}[/bright_green]" f"[dim]{'░' * (10 - n_fill)}[/dim]"
                if pct >= 80:
                    ps = f"[bright_green]{pct}%[/bright_green]"
                elif pct >= 40:
                    ps = f"[yellow]{pct}%[/yellow]"
                else:
                    ps = f"[red]{pct}%[/red]"
                rtable.add_row(
                    f["field"],
                    f"{f['total']:,}",
                    str(f["symbols"]),
                    bar,
                    ps,
                )

            verify_console.print()
            verify_console.print(rtable)

        verify_console.print()
