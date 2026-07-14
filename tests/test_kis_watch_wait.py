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
