#!/usr/bin/env -S uv run --python 3.12 --with rich --with typer python

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.forward_outcomes import analyze_forward_outcomes
from trading_agent.forward_report import write_forward_report


def main(
    session_dir: str,
    output_dir: str | None = None,
) -> None:
    source = Path(session_dir)
    snapshot_path = source / "kis_ranking_snapshots.csv"
    database_path = source / "paper_recommendations.sqlite3"
    if not source.is_dir():
        raise typer.BadParameter(f"세션 폴더를 찾을 수 없습니다: {session_dir}")
    if not snapshot_path.is_file():
        raise typer.BadParameter(f"랭킹 스냅샷을 찾을 수 없습니다: {snapshot_path}")
    if not database_path.is_file():
        raise typer.BadParameter(f"paper DB를 찾을 수 없습니다: {database_path}")
    output = source / "forward_metrics" if output_dir is None else Path(output_dir)
    outcomes = analyze_forward_outcomes(snapshot_path, database_path)
    write_forward_report(output, outcomes)
    complete = sum(row.complete for row in outcomes)
    rprint(
        f"[green]완료[/green] 선택 관찰 {len(outcomes)}건, "
        + f"완료 경로 {complete}건, 중도절단 {len(outcomes) - complete}건, {output}"
    )


if __name__ == "__main__":
    typer.run(main)
