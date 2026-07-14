#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.candidate_persistence import (
    analyze_candidate_persistence,
    write_candidate_persistence,
)


def main(risk_screen: str, output_dir: str | None = None) -> None:
    source = Path(risk_screen)
    if not source.is_file():
        raise typer.BadParameter("시장위험 CSV 파일이 필요합니다")
    output = (
        Path(output_dir)
        if output_dir is not None
        else source.with_name(f"{source.stem}_persistence")
    )
    result = analyze_candidate_persistence(source)
    write_candidate_persistence(output, result)
    rprint(
        "[green]후보 지속성 진단 완료[/green] "
        + f"스냅숏 {result.summary.snapshot_count}개, "
        + f"후보 {result.summary.candidate_count}개, {output}"
    )


if __name__ == "__main__":
    typer.run(main)
