from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_memory_store import (
    AutonomousMemoryConflictError,
    AutonomousMemoryStore,
    InvalidAutonomousMemoryStoreError,
)

NOW = dt.datetime(2026, 8, 26, 14, 30, tzinfo=dt.UTC)
TASK = hashlib.sha256(b"memory-source-task").hexdigest()


def record_fixture(**updates: object) -> AutonomousMemoryRecord:
    payload: dict[str, object] = {
        "memory_key": "market.005930.catalyst",
        "version": 1,
        "scope": AutonomousMemoryScope.MARKET,
        "summary": "Samsung Electronics catalyst evidence remains current for the present market session.",
        "fact_refs": ("fact:session-catalyst",),
        "evidence_refs": ("evidence:session-catalyst",),
        "source_task_ids": (TASK,),
        "recorded_at": NOW,
    }
    payload.update(updates)
    return AutonomousMemoryRecord.model_validate(payload)


def test_memory_id_is_canonical_deterministic_for_all_scopes() -> None:
    # Given
    records = tuple(record_fixture(scope=scope) for scope in AutonomousMemoryScope)

    # When
    identifiers = tuple(record.memory_id for record in records)

    # Then
    assert len(set(identifiers)) == len(AutonomousMemoryScope)
    assert record_fixture().memory_id == record_fixture().memory_id
    assert all(len(memory_id) == 64 for memory_id in identifiers)
    with pytest.raises(ValidationError, match="memory_id_mismatch"):
        record_fixture(memory_id="0" * 64)


def test_model_requires_evidence_source_and_factual_or_inferred_lineage() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        record_fixture(evidence_refs=())
    with pytest.raises(ValidationError):
        record_fixture(source_task_ids=())
    with pytest.raises(ValidationError, match="memory_lineage_required"):
        record_fixture(fact_refs=(), inference_refs=())
    with pytest.raises(ValidationError, match="sorted_unique_fact_refs_required"):
        record_fixture(fact_refs=("fact:z", "fact:a"))
    assert record_fixture(subject_refs=("symbol:005930",)).subject_refs == ("symbol:005930",)


def test_append_replay_conflict_and_version_lineage(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    first = record_fixture()
    second = record_fixture(
        version=2, summary="Samsung Electronics catalyst remains current after a second verification."
    )

    # When
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(first) is True
        assert writer.append(first) is False
        with pytest.raises(AutonomousMemoryConflictError, match="memory_replay_conflict"):
            writer.append(record_fixture(summary="A materially different current-session conclusion."))
        with pytest.raises(AutonomousMemoryConflictError, match="memory_version_invalid"):
            writer.append(record_fixture(version=3))
        assert writer.append(second) is True

    # Then
    assert AutonomousMemoryStore(path).reader().history(first.memory_key) == (first, second)


def test_append_rejects_scope_change_and_timestamp_regression(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    first = record_fixture()

    # When / Then
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(first)
        with pytest.raises(AutonomousMemoryConflictError, match="memory_scope_invalid"):
            writer.append(record_fixture(version=2, scope=AutonomousMemoryScope.STRATEGY))
        with pytest.raises(AutonomousMemoryConflictError, match="memory_timestamp_invalid"):
            writer.append(record_fixture(version=2, recorded_at=NOW - dt.timedelta(seconds=1)))


def test_latest_history_and_search_persist_with_bounded_subject_filter(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    old = record_fixture(subject_refs=("symbol:005930",), recorded_at=NOW)
    new = record_fixture(
        version=2,
        summary="Samsung Electronics catalyst remains current after the latest completed bar confirmation.",
        subject_refs=("symbol:005930", "theme:semiconductor"),
        recorded_at=NOW + dt.timedelta(seconds=1),
    )
    other = record_fixture(
        memory_key="strategy.005930.entry-plan",
        scope=AutonomousMemoryScope.STRATEGY,
        summary="A risk-defined entry plan remains conditional on current evidence and spread validation.",
        subject_refs=("symbol:005930",),
    )
    with AutonomousMemoryStore(path).writer() as writer:
        for record in (old, new, other):
            assert writer.append(record)

    # When
    reader = AutonomousMemoryStore(path).reader()
    found = reader.search(AutonomousMemoryScope.MARKET, ("symbol:005930",), limit=2)

    # Then
    assert reader.latest(old.memory_key) == new
    assert reader.history(old.memory_key) == (old, new)
    assert found == (new, old)
    assert reader.search(AutonomousMemoryScope.STRATEGY, ("symbol:005930",), limit=1) == (other,)
    assert reader.latest("missing.key") is None
    assert reader.history("missing.key") == ()
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="search_limit_invalid"):
        reader.search(AutonomousMemoryScope.MARKET, ("symbol:005930",), limit=0)
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="search_subject_refs_invalid"):
        reader.search(AutonomousMemoryScope.MARKET, (), limit=1)


def test_reader_returns_empty_for_absent_database(tmp_path: Path) -> None:
    # Given / When
    reader = AutonomousMemoryStore(tmp_path / "missing.sqlite3").reader()

    # Then
    assert reader.latest("market.005930.catalyst") is None
    assert reader.history("market.005930.catalyst") == ()
