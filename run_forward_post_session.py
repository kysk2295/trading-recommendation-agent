#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx2[http2,brotli,zstd]",
#   "pydantic>=2.11",
#   "rich>=13.9",
#   "typer>=0.15",
# ]
# ///
#
# ─── How to run ───
# uv run run_forward_post_session.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

import run_kis_paper_watch
from trading_agent.forward_post_session import (
    ForwardPostSessionError,
    ForwardPostSessionResult,
    close_forward_post_session,
)
from trading_agent.private_report import write_private_report
from trading_agent.strategy_factory import StrategyMode

REPORT_NAME: Final = "forward_post_session_closeout_ko.md"


def main(
    session_dir: Annotated[Path, typer.Argument()],
    session_date: Annotated[str, typer.Option()],
    minimum_watch_cycles: Annotated[int, typer.Option()] = 1,
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        parsed_date = dt.date.fromisoformat(session_date)
    except ValueError:
        raise typer.BadParameter(
            "session-date는 YYYY-MM-DD여야 합니다"
        ) from None
    if not 1 <= minimum_watch_cycles <= 390:
        raise typer.BadParameter(
            "minimum-watch-cycles는 1~390이어야 합니다"
        )
    report_root = (
        output_dir
        if output_dir is not None
        else session_dir / "post_session_closeout"
    )
    observed_at = dt.datetime.now().astimezone()
    try:
        result = close_forward_post_session(
            session_dir,
            parsed_date,
            minimum_watch_cycles=minimum_watch_cycles,
            observed_at=observed_at,
            finalizer=run_kis_paper_watch.finalize_session_output,
            runner=_run_post_session_chain,
        )
    except ForwardPostSessionError as error:
        write_private_report(
            report_root / REPORT_NAME,
            _blocked_report(parsed_date, minimum_watch_cycles, error.reason),
        )
        raise typer.Exit(code=1) from None
    write_private_report(
        report_root / REPORT_NAME,
        _complete_report(
            result,
            parsed_date,
            minimum_watch_cycles,
        ),
    )
    typer.echo(f"complete forward post-session {result.status.value}")


def _run_post_session_chain(
    session: Path,
    observed_at: dt.datetime,
) -> int | None:
    return run_kis_paper_watch.run_session_metrics(
        session,
        observed_at,
        strategy=StrategyMode.ORB,
    )


def _complete_report(
    result: ForwardPostSessionResult,
    session_date: dt.date,
    minimum_watch_cycles: int,
) -> str:
    return "\n".join(
        (
            "# Forward post-session strict closeout",
            "",
            "> Clean source recovery only; not a performance or order claim.",
            "",
            f"- result: {result.status.value}",
            f"- session date: {session_date.isoformat()}",
            f"- minimum watch cycles: {minimum_watch_cycles}",
            f"- watch cycles: {result.watch_cycles}",
            f"- ranking cycles: {result.ranking_cycles}",
            f"- retry cycles: {result.retry_cycles}",
            f"- candidate input cycles: {result.candidate_input_cycles}",
            f"- candidate inputs: {result.candidate_inputs}",
            f"- causal bars: {result.causal_bars}",
            f"- complete symbols: {result.complete_symbols}",
            f"- completed trades: {result.completed_trades}",
            f"- verified artifacts: {result.artifact_count}",
            "- failed cycle deletion: 0",
            "- quality gate relaxed: false",
            "- provider, credential, account, or order operation: 0",
            "",
        )
    )


def _blocked_report(
    session_date: dt.date,
    minimum_watch_cycles: int,
    reason: str,
) -> str:
    return "\n".join(
        (
            "# Forward post-session strict closeout",
            "",
            "> Clean source recovery only; not a performance or order claim.",
            "",
            "- result: blocked",
            f"- session date: {session_date.isoformat()}",
            f"- minimum watch cycles: {minimum_watch_cycles}",
            f"- reason: {reason}",
            "- failed cycle deletion: 0",
            "- quality gate relaxed: false",
            "- provider, credential, account, or order operation: 0",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
