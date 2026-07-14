#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich import print as rprint

from trading_agent.kis_auth import (
    KisMode,
    create_kis_client,
    get_access_token,
    load_kis_credentials,
)
from trading_agent.kis_live import (
    daytime_session_is_open,
    daytime_target_session_date,
)
from trading_agent.kis_rankings import discover_daytime_rankings, timestamp_rankings
from trading_agent.market_risk import (
    MarketRiskConfig,
    MarketRiskGate,
    fetch_active_halts,
    write_market_risk_screen,
)
from trading_agent.ranking_journal import RankingSnapshot, append_ranking_snapshot


def main(
    output_dir: str | None = None,
    top: int = 10,
    mode: KisMode = KisMode.LIVE,
) -> None:
    if not 1 <= top <= 10:
        raise typer.BadParameter("top은 1~10이어야 합니다")
    checked_at = dt.datetime.now(ZoneInfo("Asia/Seoul"))
    target_date = daytime_target_session_date(checked_at)
    if not daytime_session_is_open(checked_at) or target_date is None:
        rprint("[yellow]KIS 미국 주간거래 세션 밖이므로 랭킹을 조회하지 않습니다.[/yellow]")
        return
    output = (
        Path(output_dir)
        if output_dir is not None
        else Path("outputs/live_sessions") / checked_at.strftime("%Y%m%d")
    )
    credentials = load_kis_credentials(mode)
    with create_kis_client(mode) as client:
        token = get_access_token(client, credentials, mode)
        groups, ranking_at = timestamp_rankings(
            lambda: discover_daytime_rankings(client, credentials, token),
            lambda: dt.datetime.now().astimezone(),
        )
        halt_snapshot = fetch_active_halts(client)
        observed_at = max(ranking_at, halt_snapshot.observed_at).astimezone()
        risk_screen = MarketRiskGate(
            halt_snapshot,
            MarketRiskConfig(),
        ).screen(
            tuple(group.stocks for group in groups),
            top,
        )
    write_market_risk_screen(output / "daytime_risk_screen.csv", risk_screen)
    append_ranking_snapshot(
        output / "daytime_ranking_snapshots.csv",
        RankingSnapshot(observed_at, groups, risk_screen.selected),
    )
    map_path = output / "daytime_session_map.csv"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    has_header = map_path.is_file() and map_path.stat().st_size > 0
    with map_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(("observed_at", "target_ny_session_date"))
        writer.writerow((observed_at.isoformat(), target_date.isoformat()))
    rprint(
        f"[green]주간거래 snapshot 완료[/green] 원시 {sum(len(group.stocks) for group in groups)}행, "
        + f"위험판정 {len(risk_screen.selected) + len(risk_screen.not_selected) + len(risk_screen.rejected)}개, "
        + f"선정 {len(risk_screen.selected)}개, target_ny_session={target_date}, {output}"
    )


if __name__ == "__main__":
    typer.run(main)
