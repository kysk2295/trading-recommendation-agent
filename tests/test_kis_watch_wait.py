from __future__ import annotations

import datetime as dt
from typing import Final
from zoneinfo import ZoneInfo

import run_kis_paper_watch

NEW_YORK: Final = ZoneInfo("America/New_York")


def test_wait_for_session_open_returns_the_first_open_observation() -> None:
    observations = iter(
        (
            dt.datetime(2026, 7, 13, 9, 29, 0, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 9, 29, 30, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 9, 30, 0, tzinfo=NEW_YORK),
        )
    )
    waits: list[float] = []

    opened_at = run_kis_paper_watch.wait_for_session_open(
        lambda: next(observations),
        waits.append,
        run_kis_paper_watch.SessionWaitConfig(
            max_wait=dt.timedelta(minutes=2),
            poll_seconds=30.0,
        ),
    )

    assert opened_at == dt.datetime(2026, 7, 13, 9, 30, tzinfo=NEW_YORK)
    assert waits == [30.0, 30.0]


def test_wait_for_session_open_stops_at_the_deadline() -> None:
    observations = iter(
        (
            dt.datetime(2026, 7, 13, 9, 28, 0, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 9, 28, 30, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 9, 29, 0, tzinfo=NEW_YORK),
        )
    )
    waits: list[float] = []

    opened_at = run_kis_paper_watch.wait_for_session_open(
        lambda: next(observations),
        waits.append,
        run_kis_paper_watch.SessionWaitConfig(
            max_wait=dt.timedelta(minutes=1),
            poll_seconds=30.0,
        ),
    )

    assert opened_at is None
    assert waits == [30.0, 30.0]


def test_premarket_collection_stops_when_regular_session_opens() -> None:
    # Given: clocks spanning premarket start, two cycles, and open.
    times = iter(
        (
            dt.datetime(2026, 7, 13, 3, 59, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 4, 0, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 4, 0, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 4, 5, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 4, 5, tzinfo=NEW_YORK),
            dt.datetime(2026, 7, 13, 9, 30, tzinfo=NEW_YORK),
        )
    )
    waits: list[float] = []
    outcomes = iter((0, 1))

    # When: the collector waits, samples every five minutes, and reaches open.
    result = run_kis_paper_watch.collect_premarket_until_regular_open(
        lambda: next(times),
        waits.append,
        lambda: next(outcomes),
        run_kis_paper_watch.PremarketWaitConfig(
            max_wait=dt.timedelta(hours=8),
            closed_poll_seconds=30.0,
            collection_interval_seconds=300.0,
        ),
    )

    # Then: only premarket cycles ran and regular-open time is returned.
    assert result.opened_at == dt.datetime(
        2026,
        7,
        13,
        9,
        30,
        tzinfo=NEW_YORK,
    )
    assert result.exit_codes == (0, 1)
    assert waits == [30.0, 300.0, 300.0]


def test_premarket_collection_caps_last_wait_at_regular_open() -> None:
    # Given: a drifting five-minute cycle starts three minutes before open.
    current = dt.datetime(2026, 7, 13, 9, 27, tzinfo=NEW_YORK)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        nonlocal current
        waits.append(seconds)
        current += dt.timedelta(seconds=seconds)

    # When: the collector finishes the final premarket operation.
    result = run_kis_paper_watch.collect_premarket_until_regular_open(
        lambda: current,
        sleep,
        lambda: 0,
        run_kis_paper_watch.PremarketWaitConfig(
            max_wait=dt.timedelta(minutes=10),
            closed_poll_seconds=30.0,
            collection_interval_seconds=300.0,
        ),
    )

    # Then: the regular-session handoff occurs at open, not at 09:32.
    assert result.opened_at == dt.datetime(
        2026,
        7,
        13,
        9,
        30,
        tzinfo=NEW_YORK,
    )
    assert result.exit_codes == (0,)
    assert waits == [180.0]


def test_premarket_collection_counts_operation_time_before_open_wait() -> None:
    # Given: the final premarket provider cycle consumes two seconds.
    current = dt.datetime(2026, 7, 13, 9, 27, tzinfo=NEW_YORK)
    waits: list[float] = []

    def operate() -> int:
        nonlocal current
        current += dt.timedelta(seconds=2)
        return 0

    def sleep(seconds: float) -> None:
        nonlocal current
        waits.append(seconds)
        current += dt.timedelta(seconds=seconds)

    # When: the collector calculates the final delay after that operation.
    result = run_kis_paper_watch.collect_premarket_until_regular_open(
        lambda: current,
        sleep,
        operate,
        run_kis_paper_watch.PremarketWaitConfig(
            max_wait=dt.timedelta(minutes=10),
            closed_poll_seconds=30.0,
            collection_interval_seconds=300.0,
        ),
    )

    # Then: operation time is subtracted and handoff remains exactly at open.
    assert result.opened_at == dt.datetime(
        2026,
        7,
        13,
        9,
        30,
        tzinfo=NEW_YORK,
    )
    assert waits == [178.0]
