from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Final

import httpx2

from scr_backtest.kis_intraday import KisApiError, KisCredentials
from trading_agent.kis_provider import fetch_updown_ranking, fetch_volume_ranking
from trading_agent.ranking_journal import (
    RankingDiscovery,
    RankingFailure,
    RankingGroup,
    RankingSource,
)

US_EXCHANGES: Final = ("NAS", "NYS", "AMS")
DAYTIME_EXCHANGES: Final = ("BAQ", "BAY", "BAA")
REQUEST_DELAY_SECONDS: Final = 0.08


def discover_rankings(
    client: httpx2.Client,
    credentials: KisCredentials,
    token: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> RankingDiscovery:
    return _discover_rankings(
        client,
        credentials,
        token,
        US_EXCHANGES,
        sleeper,
    )


def discover_daytime_rankings(
    client: httpx2.Client,
    credentials: KisCredentials,
    token: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> RankingDiscovery:
    return _discover_rankings(
        client,
        credentials,
        token,
        DAYTIME_EXCHANGES,
        sleeper,
    )


def _discover_rankings(
    client: httpx2.Client,
    credentials: KisCredentials,
    token: str,
    exchanges: tuple[str, ...],
    sleeper: Callable[[float], None],
) -> RankingDiscovery:
    groups: list[RankingGroup] = []
    failures: list[RankingFailure] = []
    operations = (
        (RankingSource.UPDOWN, fetch_updown_ranking),
        (RankingSource.VOLUME, fetch_volume_ranking),
    )
    for exchange in exchanges:
        for source, operation in operations:
            try:
                stocks = operation(client, credentials, token, exchange)
            except httpx2.HTTPStatusError as error:
                failures.append(
                    RankingFailure(
                        source,
                        exchange,
                        f"HTTP {error.response.status_code}",
                    )
                )
            except httpx2.RequestError as error:
                failures.append(RankingFailure(source, exchange, type(error).__name__))
            except KisApiError as error:
                failures.append(RankingFailure(source, exchange, f"KIS {error.code}"))
            else:
                groups.append(RankingGroup(source, exchange, stocks))
            finally:
                sleeper(REQUEST_DELAY_SECONDS)
    return RankingDiscovery(tuple(groups), tuple(failures))


def timestamp_rankings(
    operation: Callable[[], RankingDiscovery],
    clock: Callable[[], dt.datetime],
) -> tuple[RankingDiscovery, dt.datetime]:
    discovery = operation()
    return discovery, clock()
