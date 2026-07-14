#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

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
from trading_agent.kis_live import premarket_session_is_open
from trading_agent.kis_rankings import discover_rankings, timestamp_rankings
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
    checked_at = dt.datetime.now(ZoneInfo("America/New_York"))
    if not premarket_session_is_open(checked_at):
        rprint("[yellow]미국 장전 세션 밖이므로 랭킹을 조회하지 않습니다.[/yellow]")
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
            lambda: discover_rankings(client, credentials, token),
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
    write_market_risk_screen(output / "premarket_risk_screen.csv", risk_screen)
    append_ranking_snapshot(
        output / "premarket_ranking_snapshots.csv",
        RankingSnapshot(observed_at, groups, risk_screen.selected),
    )
    rprint(
        f"[green]장전 snapshot 완료[/green] 원시 {sum(len(group.stocks) for group in groups)}행, "
        + f"위험통과 {len(risk_screen.selected) + len(risk_screen.not_selected)}개, "
        + f"선정 {len(risk_screen.selected)}개, {output}"
    )


if __name__ == "__main__":
    typer.run(main)
