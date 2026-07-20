from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.us_news_catalyst_day_session_audit import (
    UsNewsCatalystDaySessionEvent,
    UsNewsCatalystDaySessionEventRequest,
    UsNewsCatalystDaySessionEventStatus,
    UsNewsCatalystDaySessionPhase,
    build_us_news_catalyst_day_session_event,
)
from trading_agent.us_news_catalyst_day_session_store import (
    UsNewsCatalystDaySessionStore,
    UsNewsCatalystDaySessionWriterLeaseUnavailableError,
)

SESSION_ID = "a" * 64
OBSERVED = dt.datetime(2026, 7, 21, 14, tzinfo=dt.UTC)


def test_day_session_store_appends_verified_hash_chain(tmp_path: Path) -> None:
    store = UsNewsCatalystDaySessionStore(tmp_path / "audit.sqlite3")
    first = _event(1, None, UsNewsCatalystDaySessionPhase.REGISTER)
    second = _event(2, first.event_id, UsNewsCatalystDaySessionPhase.START)

    with store.writer() as writer:
        assert writer.append(first) is True
        assert writer.append(second) is True
        assert writer.append(second) is False

    assert store.events(SESSION_ID) == (first, second)
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_day_session_store_lease_is_nonblocking(tmp_path: Path) -> None:
    store = UsNewsCatalystDaySessionStore(tmp_path / "audit.sqlite3")

    with store.writer(), pytest.raises(
        UsNewsCatalystDaySessionWriterLeaseUnavailableError
    ), store.writer():
        pytest.fail("second writer lease must not open")


def _event(
    sequence: int,
    previous: str | None,
    phase: UsNewsCatalystDaySessionPhase,
) -> UsNewsCatalystDaySessionEvent:
    return build_us_news_catalyst_day_session_event(
        UsNewsCatalystDaySessionEventRequest(
            session_id=SESSION_ID,
            phase=phase,
            observed_at=OBSERVED + dt.timedelta(seconds=sequence),
            status=UsNewsCatalystDaySessionEventStatus.COMPLETED,
            command_exit_code=0,
            evidence_sha256=f"{sequence:x}" * 64,
            reason_code=None,
        ),
        sequence,
        previous,
    )
