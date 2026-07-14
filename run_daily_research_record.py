#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from trading_agent.daily_research_ledger import (
    build_daily_record,
    write_daily_record,
)
from trading_agent.strategy_factory import StrategyMode


def main(
    input_dir: str,
    session_date: Annotated[str, typer.Option("--session-date")],
    strategy: StrategyMode = StrategyMode.ORB,
    code_version: str | None = None,
) -> None:
    session = Path(input_dir)
    if not session.is_dir():
        raise typer.BadParameter(f"세션 폴더를 찾을 수 없습니다: {input_dir}")
    try:
        parsed_date = dt.date.fromisoformat(session_date)
    except ValueError as error:
        raise typer.BadParameter("session-date는 YYYY-MM-DD여야 합니다") from error
    revision = code_version
    if revision is None:
        project = Path(__file__).parent
        commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        revision = commit + ("+dirty" if dirty else "")
    record = build_daily_record(
        session,
        parsed_date,
        strategy,
        revision,
        dt.datetime.now().astimezone(),
    )
    created = write_daily_record(session, record)
    rprint(
        f"[green]완료[/green] record={record.record_id[:12]}, "
        + f"created={created}, eligible={record.session_quality.forward_day_eligible}, "
        + f"promotion={record.promotion.allowed}, {session}"
    )


if __name__ == "__main__":
    typer.run(main)
