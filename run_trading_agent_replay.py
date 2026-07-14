#!/usr/bin/env -S uv run --python 3.12 --with typer --with rich python

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.engine import RecommendationEngine
from trading_agent.models import BarInput
from trading_agent.replay import load_bars, write_alert_outbox, write_report
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig


def main(
    input_csv: str,
    output_dir: str = "outputs/trading_agent_replay",
    range_minutes: int = 5,
) -> None:
    source = Path(input_csv)
    if not source.is_file():
        raise typer.BadParameter(f"입력 CSV를 찾을 수 없습니다: {input_csv}")
    if range_minutes < 1:
        raise typer.BadParameter("range-minutes는 1 이상이어야 합니다")
    output = Path(output_dir)
    database = output / "paper_recommendations.sqlite3"
    if database.exists():
        raise typer.BadParameter(f"기존 감사 로그가 있습니다. 새 출력 폴더를 사용하세요: {database}")
    store = PaperStore(database)
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig()),
        OpeningRangeBreakout(OrbConfig(range_minutes=range_minutes)),
        RiskConfig(),
        store,
    )
    bars = load_bars(source)
    last_bars: dict[tuple[str, dt.date], BarInput] = {}
    for bar in bars:
        _ = engine.process(bar)
        last_bars[(bar.symbol, bar.timestamp.date())] = bar
    for bar in last_bars.values():
        engine.finalize_day(bar)
    write_report(output / "recommendations_ko.md", store)
    recommendations = store.recommendations()
    projection_started_at = min(
        (row.created_at for row in recommendations),
        default=dt.datetime.now().astimezone(),
    )
    queued = write_alert_outbox(output, store, projection_started_at)
    rprint(f"[green]완료[/green] 분봉 {len(bars)}개, " + f"추천 {len(recommendations)}개, 신규 카드 {queued}개")


if __name__ == "__main__":
    typer.run(main)
