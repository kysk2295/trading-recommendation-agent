from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from trading_agent.kr_theme_day_session_audit import (
    InvalidKrThemeDaySessionAuditError,
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEventRequest,
    KrThemeDaySessionPhaseStatus,
    build_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_evidence import (
    InvalidKrThemeDaySessionEvidenceError,
    KrThemeDaySessionSourceState,
    build_kr_theme_day_session_source_attestation,
)
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore

NOW = dt.datetime(2026, 7, 20, 9, 4, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def test_audit_chains_events_and_exact_replay_is_noop(tmp_path: Path) -> None:
    # Given
    audit_path = tmp_path / "audit.sqlite3"
    store = KrThemeDaySessionAuditStore(audit_path)
    first = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            "a" * 64,
            KrThemeDaySessionPhase.INTRADAY_COLLECT,
            "2026-07-20T09:04+09:00",
            NOW,
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        1,
        None,
    )

    # When
    created = store.append(first)
    replay = store.append(first)
    second = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            "a" * 64,
            KrThemeDaySessionPhase.INTRADAY_ENTRY,
            "2026-07-20T09:04+09:00",
            NOW,
            KrThemeDaySessionPhaseStatus.BLOCKED,
            1,
        ),
        2,
        first.event_id,
    )
    assert store.append(second) is True

    # Then
    assert created is True
    assert replay is False
    assert store.events("a" * 64) == (first, second)


def test_completed_event_attests_source_state_and_exact_replay(tmp_path: Path) -> None:
    # Given
    audit_path = tmp_path / "audit.sqlite3"
    store = KrThemeDaySessionAuditStore(audit_path)
    event = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            "c" * 64,
            KrThemeDaySessionPhase.REGISTER,
            "session",
            NOW,
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        1,
        None,
    )
    assert store.append(event) is True
    attestation = build_kr_theme_day_session_source_attestation(
        event,
        KrThemeDaySessionSourceState("d" * 64, 1),
    )

    # When
    evidence = KrThemeDaySessionEvidenceStore(audit_path)
    created = evidence.append(attestation)
    replay = evidence.append(attestation)

    # Then
    assert created is True
    assert replay is False
    assert evidence.attestations("c" * 64) == (attestation,)
    changed = build_kr_theme_day_session_source_attestation(
        event,
        KrThemeDaySessionSourceState("e" * 64, 1),
    )
    with pytest.raises(InvalidKrThemeDaySessionEvidenceError):
        _ = evidence.append(changed)
    with sqlite3.connect(evidence.path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute("UPDATE kr_theme_day_session_source_attestations SET phase='start'")
    evidence.path.chmod(0o644)
    with pytest.raises(InvalidKrThemeDaySessionEvidenceError):
        _ = evidence.attestations("c" * 64)


def test_audit_rejects_chain_and_sql_tamper(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "audit.sqlite3"
    store = KrThemeDaySessionAuditStore(path)
    event = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            "b" * 64,
            KrThemeDaySessionPhase.REGISTER,
            "session",
            NOW,
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        1,
        None,
    )
    assert store.append(event) is True

    # When / Then
    bad = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            "b" * 64,
            KrThemeDaySessionPhase.START,
            "session",
            NOW,
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        3,
        event.event_id,
    )
    with pytest.raises(InvalidKrThemeDaySessionAuditError):
        _ = store.append(bad)
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute("UPDATE kr_theme_day_session_events SET exit_code=9")
