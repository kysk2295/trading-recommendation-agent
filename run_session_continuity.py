#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.session_continuity import (
    SessionFiles,
    analyze_session_continuity,
    write_continuity_outputs,
)


def main(session_dir: str, output_dir: str | None = None) -> None:
    session = Path(session_dir)
    files = SessionFiles(
        _existing(session / "daytime_risk_screen.csv"),
        _existing(session / "premarket_risk_screen.csv"),
        _existing(session / "market_risk_screen.csv"),
    )
    if files.daytime is None and files.premarket is None and files.regular is None:
        raise typer.BadParameter("세션 위험판정 CSV가 하나 이상 필요합니다")
    output = Path(output_dir) if output_dir is not None else session / "session_continuity"
    result = analyze_session_continuity(files)
    write_continuity_outputs(output, result)
    rprint(
        f"[green]세션 연속성 진단 완료[/green] 후보 {len(result.candidates)}개, "
        + f"전환 {len(result.summaries)}개, {output}"
    )


def _existing(path: Path) -> Path | None:
    return path if path.is_file() else None


if __name__ == "__main__":
    typer.run(main)
