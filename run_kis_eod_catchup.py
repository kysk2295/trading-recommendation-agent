#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from rich import print as rprint

from scr_backtest.kis_http import begin_retry_capture, captured_retry_events, end_retry_capture
from scr_backtest.kis_intraday import KisSession
from trading_agent.bar_archive import tracked_candidates_for_session
from trading_agent.engine import RecommendationEngine
from trading_agent.kis_auth import KisMode, create_kis_client, get_access_token, load_kis_credentials
from trading_agent.kis_eod import EodCatchupRun, append_eod_artifacts, catch_up_candidates, duplicate_symbols
from trading_agent.kis_live import regular_session_bounds
from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_retry_audit import append_kis_retry_audit
from trading_agent.kis_scan import KisPaperScanner
from trading_agent.replay import write_report
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy_factory import StrategyMode, build_strategy


def main(
    output_dir: Annotated[str, typer.Option()],
    mode: KisMode = KisMode.LIVE,
    strategy: StrategyMode = StrategyMode.ORB,
    max_pages: int = 1,
) -> None:
    if not 1 <= max_pages <= 10:
        raise typer.BadParameter("max-pages는 1~10이어야 합니다")
    observed_at = dt.datetime.now(ZoneInfo("America/New_York"))
    session_date = observed_at.date()
    bounds = regular_session_bounds(session_date)
    if bounds is None or observed_at < bounds[1]:
        rprint("[red]정규장 종료 뒤에만 EOD catch-up을 실행할 수 있습니다.[/red]")
        raise typer.Exit(code=2)
    output = Path(output_dir)
    database = output / "paper_recommendations.sqlite3"
    if not database.is_file():
        raise typer.BadParameter(f"paper DB를 찾을 수 없습니다: {database}")
    candidates = tracked_candidates_for_session(database, session_date)
    duplicates = duplicate_symbols(candidates)
    if duplicates:
        rprint(f"[red]거래소 간 중복 ticker를 거부합니다: {', '.join(duplicates)}[/red]")
        raise typer.Exit(code=2)
    if not candidates:
        result = EodCatchupRun(session_date, observed_at, ())
        append_kis_retry_audit(output, observed_at, (), artifact_prefix="eod_kis_read_retry")
    else:
        result = _run_network_catchup(
            output,
            candidates,
            mode,
            strategy,
            max_pages,
            session_date,
            observed_at,
        )
    append_eod_artifacts(output, result)
    store = PaperStore(database)
    write_report(output / "recommendations_ko.md", store)
    rprint(
        f"[green]EOD catch-up[/green] 후보 {result.candidate_count}개, "
        + f"완전 {result.complete_count}개, 실패 {result.failure_count}개, {output}"
    )
    if result.failure_count:
        raise typer.Exit(code=1)


def _run_network_catchup(
    output: Path,
    candidates: tuple[KisRankedStock, ...],
    mode: KisMode,
    strategy: StrategyMode,
    max_pages: int,
    session_date: dt.date,
    observed_at: dt.datetime,
) -> EodCatchupRun:
    credentials = load_kis_credentials(mode)
    retry_capture = begin_retry_capture()
    try:
        with create_kis_client(mode) as client:
            token = get_access_token(client, credentials, mode)
            store = PaperStore(output / "paper_recommendations.sqlite3")
            scanner = KisPaperScanner(
                client,
                KisSession(credentials, token),
                RecommendationEngine(
                    MomentumScanner(ScannerConfig()),
                    build_strategy(strategy, range_minutes=5),
                    RiskConfig(),
                    store,
                ),
            )
            return catch_up_candidates(scanner, candidates, max_pages, session_date, observed_at)
    finally:
        events = captured_retry_events()
        end_retry_capture(retry_capture)
        append_kis_retry_audit(output, observed_at, events, artifact_prefix="eod_kis_read_retry")


if __name__ == "__main__":
    typer.run(main)
