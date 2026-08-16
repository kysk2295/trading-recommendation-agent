from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    CycleId,
    EvidenceId,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_cycle_store import (
    InvalidResearchAgentCycleStoreError,
    ResearchAgentCycleStore,
    ResearchAgentCycleWriterLeaseUnavailableError,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence(family: AgentFamilyId, sequence: int) -> ResearchAgentEvidenceV1:
    digest = hashlib.sha256(f"{family}:{sequence}".encode()).hexdigest()
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(digest),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"source.{family}.{sequence}",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="us_equities",
    )


def _result(cycle_id: CycleId, family: AgentFamilyId) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle_id),
        cycle_id=cycle_id,
        agent_family_id=family,
        market_id="us_equities",
        status=ResearchAgentResultStatus.COMPLETED,
        question="Does the current evidence justify another bounded action?",
        summary="The bounded research cycle completed without broker authority.",
        reason=None,
        continuation=None,
        evidence_refs=("a" * 64,),
        artifact_refs=("b" * 64,),
        occurred_at=NOW + dt.timedelta(minutes=1),
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
    )


def test_terminal_cycle_advances_only_its_actor_cursor(tmp_path: Path) -> None:
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        evidence = _evidence("opportunity_manager", 1)
        assert store.append_evidence(evidence)
        stored = store.runnable_evidence("opportunity_manager", NOW)[0]
        started = store.start_cycle(stored, NOW)
        assert store.cursor("opportunity_manager") == 0

        result = _result(started.cycle_id, "opportunity_manager")
        store.finish_cycle(started, result)
        store.finish_cycle(started, result)

        assert store.cursor("opportunity_manager") == stored.sequence
        assert store.cursor("systematic_quant") == 0
        assert store.runnable_evidence("opportunity_manager", NOW) == ()
        assert store.results() == (result,)


def test_restart_interrupts_started_cycle_without_duplicate_action(tmp_path: Path) -> None:
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        evidence = _evidence("systematic_quant", 1)
        store.append_evidence(evidence)
        stored = store.runnable_evidence("systematic_quant", NOW)[0]
        started = store.start_cycle(stored, NOW)

        recovered = store.recover_interrupted(NOW + dt.timedelta(minutes=1))
        replay = store.start_cycle(stored, NOW + dt.timedelta(minutes=2))

        assert recovered == (started.cycle_id,)
        assert replay.action_request_id == started.action_request_id
        assert replay.started_at == NOW + dt.timedelta(minutes=2)
        assert [event.state for event in store.cycle_events(started.cycle_id)] == [
            "started",
            "interrupted",
            "started",
        ]


def test_evidence_identity_is_idempotent_but_conflicts_fail_closed(tmp_path: Path) -> None:
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        evidence = _evidence("market_context", 1)
        assert store.append_evidence(evidence)
        assert not store.append_evidence(evidence)
        conflict = evidence.model_copy(update={"source_key": "source.market_context.conflict"})

        with pytest.raises(InvalidResearchAgentCycleStoreError, match="evidence_identity_conflict"):
            store.append_evidence(conflict)


def test_store_reads_legacy_hash_only_evidence(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    with ResearchAgentCycleStore(path):
        pass
    evidence = _evidence("market_context", 1)
    payload = evidence.model_dump(mode="json")
    for key in ("bounded_payload_json", "payload_truncated", "subject_refs"):
        del payload[key]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
            (
                evidence.evidence_id,
                evidence.agent_family_id,
                evidence.available_at.isoformat(),
                json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            ),
        )
    with ResearchAgentCycleStore(path) as store:
        restored = store.runnable_evidence("market_context", NOW)[0].evidence

    assert restored.bounded_payload_json is None
    assert restored.subject_refs == ()


def test_store_is_private_rejects_symlink_and_holds_one_writer_lease(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    with ResearchAgentCycleStore(path):
        assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ResearchAgentCycleWriterLeaseUnavailableError):
            ResearchAgentCycleStore(path)

    target = tmp_path / "target.sqlite3"
    target.touch(mode=0o600)
    symlink = tmp_path / "symlink.sqlite3"
    symlink.symlink_to(target)
    with pytest.raises(InvalidResearchAgentCycleStoreError, match="database_path_invalid"):
        ResearchAgentCycleStore(symlink)


def test_store_rejects_an_unknown_existing_schema(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    os.chmod(path, 0o600)

    with pytest.raises(InvalidResearchAgentCycleStoreError, match="schema_version_invalid"):
        ResearchAgentCycleStore(path)
