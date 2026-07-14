from __future__ import annotations

import datetime as dt

from trading_agent.kis_rankings import timestamp_rankings
from trading_agent.ranking_journal import RankingGroup


def test_ranking_observation_time_is_captured_after_the_response() -> None:
    events: list[str] = []
    timestamp = dt.datetime(2026, 7, 10, 9, 35, tzinfo=dt.UTC)

    def load() -> tuple[RankingGroup, ...]:
        events.append("ranking_response")
        return ()

    def clock() -> dt.datetime:
        events.append("observed_at")
        return timestamp

    groups, observed_at = timestamp_rankings(load, clock)

    assert groups == ()
    assert observed_at == timestamp
    assert events == ["ranking_response", "observed_at"]
