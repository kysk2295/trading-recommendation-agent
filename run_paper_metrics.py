#!/usr/bin/env -S uv run --python 3.12 --with rich --with typer python

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.metrics import extract_paper_trades
from trading_agent.metrics_report import write_metrics_report
from trading_agent.store import PaperStore


def main(
    input_path: str,
    output_dir: str = "outputs/paper_metrics",
) -> None:
    source = Path(input_path)
    if not source.exists():
        raise typer.BadParameter(f"입력 경로를 찾을 수 없습니다: {input_path}")
    databases = (
        (source,)
        if source.is_file()
        else tuple(sorted(source.rglob("paper_recommendations.sqlite3")))
    )
    stores = tuple(PaperStore(path) for path in databases)
    trades = extract_paper_trades(stores)
    summaries = write_metrics_report(Path(output_dir), trades)
    rprint(
        f"[green]완료[/green] DB {len(databases)}개, 거래 {len(trades)}개, "
        + f"비용 시나리오 {len(summaries)}개, {output_dir}"
    )


if __name__ == "__main__":
    typer.run(main)
