from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Final

import httpx2

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_provider import fetch_updown_ranking, fetch_volume_ranking
from trading_agent.ranking_journal import RankingGroup, RankingSource

US_EXCHANGES: Final = ("NAS", "NYS", "AMS")
DAYTIME_EXCHANGES: Final = ("BAQ", "BAY", "BAA")
REQUEST_DELAY_SECONDS: Final = 0.08


def discover_rankings(
    client: httpx2.Client,
    credentials: KisCredentials,
    token: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[RankingGroup, ...]:
    groups: list[RankingGroup] = []
    for exchange in US_EXCHANGES:
        groups.append(
            RankingGroup(
                RankingSource.UPDOWN,
                exchange,
                fetch_updown_ranking(client, credentials, token, exchange),
            )
        )
        sleeper(REQUEST_DELAY_SECONDS)
        groups.append(
            RankingGroup(
                RankingSource.VOLUME,
                exchange,
                fetch_volume_ranking(client, credentials, token, exchange),
            )
        )
        sleeper(REQUEST_DELAY_SECONDS)
    return tuple(groups)


def discover_daytime_rankings(
    client: httpx2.Client,
    credentials: KisCredentials,
    token: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[RankingGroup, ...]:
    groups: list[RankingGroup] = []
    for exchange in DAYTIME_EXCHANGES:
        groups.append(
            RankingGroup(
                RankingSource.UPDOWN,
                exchange,
                fetch_updown_ranking(client, credentials, token, exchange),
            )
        )
        sleeper(REQUEST_DELAY_SECONDS)
        groups.append(
            RankingGroup(
                RankingSource.VOLUME,
                exchange,
                fetch_volume_ranking(client, credentials, token, exchange),
            )
        )
        sleeper(REQUEST_DELAY_SECONDS)
    return tuple(groups)


def timestamp_rankings(
    operation: Callable[[], tuple[RankingGroup, ...]],
    clock: Callable[[], dt.datetime],
) -> tuple[tuple[RankingGroup, ...], dt.datetime]:
    groups = operation()
    return groups, clock()
