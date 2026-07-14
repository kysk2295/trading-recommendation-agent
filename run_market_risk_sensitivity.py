#!/usr/bin/env -S uv run --python 3.12 python
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "rich>=13.9", "typer>=0.15"]
# ///

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.risk_sensitivity import (
    adjacent_risk_configs,
    analyze_risk_sensitivity,
    load_risk_candidates,
    write_risk_sensitivity,
)


def main(input_path: str, output_dir: str | None = None) -> None:
    source = Path(input_path)
    if not source.exists():
        raise typer.BadParameter(f"입력 경로를 찾을 수 없습니다: {input_path}")
    paths = (
        (source,)
        if source.is_file()
        else tuple(sorted(source.rglob("market_risk_screen.csv")))
    )
    if not paths:
        raise typer.BadParameter(f"시장위험 CSV를 찾을 수 없습니다: {input_path}")
    output = (
        source.parent / "market_risk_sensitivity"
        if source.is_file()
        else source / "market_risk_sensitivity"
    ) if output_dir is None else Path(output_dir)
    candidates = load_risk_candidates(paths)
    result = analyze_risk_sensitivity(candidates, adjacent_risk_configs())
    write_risk_sensitivity(output, result, paths)
    snapshots = len({candidate.observed_at for candidate in candidates})
    rprint(
        f"[green]완료[/green] 입력 {len(paths)}개, 스냅샷 {snapshots}개, "
        + f"후보 {len(candidates)}개, 인접 조합 {len(result.summaries)}개, {output}"
    )


if __name__ == "__main__":
    typer.run(main)
