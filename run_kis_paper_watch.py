#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich import print as rprint

from trading_agent.engine import finalize_due_recommendations
from trading_agent.kis_eod_watch import EodWaitConfig, eod_catchup_command, wait_for_eod_ready
from trading_agent.kis_live import (
    regular_session_is_open,
)
from trading_agent.kis_watch_wait import (
    PremarketWaitConfig,
    SessionWaitConfig,
    collect_premarket_until_regular_open,
    wait_for_session_open,
)
from trading_agent.replay import write_report
from trading_agent.scan_cycle import (
    CycleRuntime,
    WatchConfig,
    append_cycle_audit,
    run_cycles,
)
from trading_agent.store import PaperStore
from trading_agent.strategy_factory import StrategyMode


def finalize_session_output(output: Path, observed_at: dt.datetime) -> int:
    database = output / "paper_recommendations.sqlite3"
    if not database.is_file():
        return 0
    store = PaperStore(database)
    finalized = finalize_due_recommendations(store, observed_at)
    if finalized:
        write_report(output / "recommendations_ko.md", store)
    return finalized


def _scan_command(
    output: Path,
    strategy: StrategyMode,
    top: int,
    max_pages: int,
) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kis_paper_scan.py")),
        "--output-dir",
        str(output),
        "--strategy",
        strategy.value,
        "--top",
        str(top),
        "--max-pages",
        str(max_pages),
    )


def _premarket_scan_command(output: Path, top: int) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_kis_premarket_scan.py")),
        "--output-dir",
        str(output),
        "--top",
        str(top),
    )


def _paper_metrics_command(output: Path) -> tuple[str, ...]:
    return (
        str(Path(__file__).with_name("run_paper_metrics.py")),
        str(output / "paper_recommendations.sqlite3"),
        "--output-dir",
        str(output / "paper_metrics"),
    )


def _daily_research_command(
    output: Path,
    observed_at: dt.datetime,
    strategy: StrategyMode,
) -> tuple[str, ...]:
    session_date = observed_at.astimezone(ZoneInfo("America/New_York")).date()
    return (
        str(Path(__file__).with_name("run_daily_research_record.py")),
        str(output),
        "--session-date",
        session_date.isoformat(),
        "--strategy",
        strategy.value,
    )


def _run_and_audit(command: tuple[str, ...], audit_path: Path) -> int:
    started_at = dt.datetime.now().astimezone()
    completed = subprocess.run(command, check=False)
    append_cycle_audit(audit_path, started_at, completed.returncode)
    return completed.returncode


def run_session_metrics(
    output: Path,
    observed_at: dt.datetime,
    runner: Callable[[tuple[str, ...], Path], int] = _run_and_audit,
    strategy: StrategyMode = StrategyMode.ORB,
) -> int | None:
    database = output / "paper_recommendations.sqlite3"
    if regular_session_is_open(observed_at) or not database.is_file():
        return None
    metrics_exit_code = runner(
        _paper_metrics_command(output),
        output / "post_session_metrics_cycles.csv",
    )
    if metrics_exit_code:
        return metrics_exit_code
    return runner(
        _daily_research_command(output, observed_at, strategy),
        output / "post_session_research_cycles.csv",
    )


def main(
    output_dir: str | None = None,
    cycles: int = 390,
    interval_seconds: float = 60.0,
    wait_until_open: bool = False,
    max_wait_minutes: int = 720,
    strategy: StrategyMode = StrategyMode.ORB,
    top: int = 10,
    max_pages: int = 1,
    collect_premarket: bool = False,
    premarket_interval_seconds: float = 300.0,
) -> None:
    if not 1 <= cycles <= 390:
        raise typer.BadParameter("cycles는 1~390이어야 합니다")
    if not 1.0 <= interval_seconds <= 3600.0:
        raise typer.BadParameter("interval-seconds는 1~3600이어야 합니다")
    if not 1 <= max_wait_minutes <= 1440:
        raise typer.BadParameter("max-wait-minutes는 1~1440이어야 합니다")
    if not 1 <= top <= 10:
        raise typer.BadParameter("top은 1~10이어야 합니다")
    if not 1 <= max_pages <= 10:
        raise typer.BadParameter("max-pages는 1~10이어야 합니다")
    if not 60.0 <= premarket_interval_seconds <= 3600.0:
        raise typer.BadParameter("premarket-interval-seconds는 60~3600이어야 합니다")
    checked_at = dt.datetime.now(ZoneInfo("America/New_York"))
    output = (
        Path(output_dir) if output_dir is not None else Path("outputs/live_sessions") / checked_at.strftime("%Y%m%d")
    )
    premarket_exit_codes: tuple[int, ...] = ()
    if not regular_session_is_open(checked_at):
        if not wait_until_open and not collect_premarket:
            rprint("[yellow]미국 정규장 밖이므로 감시를 시작하지 않습니다.[/yellow]")
            return
        if collect_premarket:
            rprint("[yellow]미국 장전 랭킹 수집과 정규장 개장을 기다립니다.[/yellow]")
            premarket_result = collect_premarket_until_regular_open(
                lambda: dt.datetime.now(ZoneInfo("America/New_York")),
                time.sleep,
                lambda: _run_and_audit(
                    _premarket_scan_command(output, top),
                    output / "premarket_watch_cycles.csv",
                ),
                PremarketWaitConfig(
                    max_wait=dt.timedelta(minutes=max_wait_minutes),
                    closed_poll_seconds=30.0,
                    collection_interval_seconds=premarket_interval_seconds,
                ),
            )
            opened_at = premarket_result.opened_at
            premarket_exit_codes = premarket_result.exit_codes
        else:
            rprint("[yellow]미국 정규장 개장을 기다립니다.[/yellow]")
            opened_at = wait_for_session_open(
                lambda: dt.datetime.now(ZoneInfo("America/New_York")),
                time.sleep,
                SessionWaitConfig(
                    max_wait=dt.timedelta(minutes=max_wait_minutes),
                    poll_seconds=30.0,
                ),
            )
        if opened_at is None:
            rprint("[red]대기 제한 안에 미국 정규장이 열리지 않았습니다.[/red]")
            raise typer.Exit(code=2)
        checked_at = opened_at

    def scan_once() -> int:
        return _run_and_audit(
            _scan_command(output, strategy, top, max_pages),
            output / "watch_cycles.csv",
        )

    exit_codes = run_cycles(
        scan_once,
        WatchConfig(cycles, interval_seconds),
        CycleRuntime(
            time.sleep,
            lambda: regular_session_is_open(dt.datetime.now(ZoneInfo("America/New_York"))),
        ),
    )
    session_date = checked_at.astimezone(ZoneInfo("America/New_York")).date()
    eod_ready_at = wait_for_eod_ready(
        lambda: dt.datetime.now(ZoneInfo("America/New_York")),
        time.sleep,
        session_date,
        EodWaitConfig(
            max_wait=dt.timedelta(minutes=3),
            poll_seconds=15.0,
            settlement_delay=dt.timedelta(seconds=65),
        ),
    )
    eod_exit_code = None
    if eod_ready_at is not None:
        eod_exit_code = _run_and_audit(
            eod_catchup_command(Path(__file__).parent, output, strategy, max_pages),
            output / "eod_catchup_cycles.csv",
        )
    ended_at = dt.datetime.now(ZoneInfo("America/New_York"))
    finalized = finalize_session_output(output, ended_at)
    metrics_exit_code = run_session_metrics(
        output,
        ended_at,
        strategy=strategy,
    )
    failures = sum(code != 0 for code in (*premarket_exit_codes, *exit_codes))
    failures += int(eod_exit_code not in (None, 0))
    failures += int(metrics_exit_code not in (None, 0))
    metrics_status = "skipped" if metrics_exit_code is None else str(metrics_exit_code)
    eod_status = "skipped" if eod_exit_code is None else str(eod_exit_code)
    rprint(
        f"[green]감시 종료[/green] premarket_cycles={len(premarket_exit_codes)}, "
        + f"regular_cycles={len(exit_codes)}, "
        + f"failures={failures}, time_exits={finalized}, "
        + f"eod_exit={eod_status}, metrics_exit={metrics_status}, {output}"
    )
    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
