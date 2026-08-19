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
    MarketId,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_cycle_schema import RESEARCH_AGENT_CYCLE_SCHEMA_V1
from trading_agent.research_agent_cycle_store import (
    InvalidResearchAgentCycleStoreError,
    ResearchAgentCycleStore,
    ResearchAgentCycleWriterLeaseUnavailableError,
)
from trading_agent.research_agent_cycle_store_codec import result_from_payload
from trading_agent.research_agent_runtime_support import retry_evidence

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence(
    family: AgentFamilyId,
    sequence: int,
    market_id: MarketId = "us_equities",
) -> ResearchAgentEvidenceV1:
    digest = hashlib.sha256(f"{family}:{market_id}:{sequence}".encode()).hexdigest()
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(digest),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"source.{family}.{sequence}",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id=market_id,
    )


def _result(
    cycle_id: CycleId,
    family: AgentFamilyId,
    market_id: MarketId = "us_equities",
) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle_id),
        cycle_id=cycle_id,
        agent_family_id=family,
        market_id=market_id,
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
    bounded_payload_json = '{"symbol":"SPY"}'
    evidence = ResearchAgentEvidenceV1.model_validate(
        evidence.model_dump(mode="python")
        | {
            "bounded_payload_json": bounded_payload_json,
            "payload_sha256": hashlib.sha256(bounded_payload_json.encode()).hexdigest(),
            "subject_refs": ("SPY",),
        }
    )
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
        replayed = store.append_evidence(evidence)

    assert restored.bounded_payload_json is None
    assert restored.subject_refs == ()
    assert not replayed


def test_result_decoder_preserves_shipped_no_action_artifacts() -> None:
    result = ResearchAgentResultV1(
        result_id=research_agent_result_id(CycleId("d" * 64)),
        cycle_id=CycleId("d" * 64),
        agent_family_id="market_context",
        market_id="us_equities",
        status=ResearchAgentResultStatus.NO_ACTION,
        question="Did the current market regime change materially?",
        summary="No completed eligible context change exists.",
        reason="no_material_change",
        continuation="Wait for the next completed market observation.",
        evidence_refs=("a" * 64,),
        artifact_refs=(),
        occurred_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
    )
    payload = result.model_dump(mode="json")
    payload["artifact_refs"] = ["b" * 64]

    with pytest.raises(InvalidResearchAgentCycleStoreError, match="stored_result_invalid"):
        result_from_payload(json.dumps(payload))

    del payload["decision_kind"]
    restored = result_from_payload(json.dumps(payload))

    assert restored.status is ResearchAgentResultStatus.NO_ACTION
    assert restored.artifact_refs == ("b" * 64,)
    assert "decision_kind" not in restored.model_fields_set


def test_all_evidence_returns_canonical_insertion_order(tmp_path: Path) -> None:
    first = _evidence("market_context", 1)
    second = _evidence("opportunity_manager", 2)
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        assert store.append_evidence(first)
        assert store.append_evidence(second)

        assert store.all_evidence() == (first, second)


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


def test_day_cursors_are_market_partitioned_for_interleaved_evidence(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    us_first = _evidence("day_trading", 1, "us_equities")
    kr = _evidence("day_trading", 2, "kr_equities")
    us_second = _evidence("day_trading", 3, "us_equities")
    with ResearchAgentCycleStore(path) as store:
        for evidence in (us_first, kr, us_second):
            assert store.append_evidence(evidence)
        first = store.runnable_evidence("day_trading", NOW)[0]
        first_cycle = store.start_cycle(first, NOW)
        store.finish_cycle(first_cycle, _result(first_cycle.cycle_id, "day_trading"))

        pending = store.runnable_evidence("day_trading", NOW)
        assert tuple(item.evidence.market_id for item in pending) == (
            "kr_equities",
            "us_equities",
        )
        assert store.day_cursor("us_equities") == first.sequence
        assert store.day_cursor("kr_equities") == 0


def test_v1_cycle_database_migrates_without_losing_existing_evidence(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    evidence = _evidence("market_context", 1)
    with sqlite3.connect(path) as connection:
        for statement in RESEARCH_AGENT_CYCLE_SCHEMA_V1:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
            (
                evidence.evidence_id,
                evidence.agent_family_id,
                evidence.available_at.isoformat(),
                json.dumps(
                    evidence.model_dump(mode="json"),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
    os.chmod(path, 0o600)

    with ResearchAgentCycleStore(path) as store:
        assert store.runnable_evidence("market_context", NOW)[0].evidence == evidence
        assert store.day_cursor("us_equities") == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def test_v1_day_cursor_migration_preserves_consumed_position_per_market(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cycles.sqlite3"
    consumed_us = _evidence("day_trading", 1, "us_equities")
    consumed_kr = _evidence("day_trading", 2, "kr_equities")
    pending_us = _evidence("day_trading", 3, "us_equities")
    with sqlite3.connect(path) as connection:
        for statement in RESEARCH_AGENT_CYCLE_SCHEMA_V1:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        for evidence in (consumed_us, consumed_kr, pending_us):
            connection.execute(
                "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
                (
                    evidence.evidence_id,
                    evidence.agent_family_id,
                    evidence.available_at.isoformat(),
                    json.dumps(
                        evidence.model_dump(mode="json"),
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
        connection.execute("INSERT INTO cursors(agent_family_id,evidence_sequence) VALUES('day_trading',2)")
    os.chmod(path, 0o600)

    with ResearchAgentCycleStore(path) as store:
        assert store.day_cursor("us_equities") == 1
        assert store.day_cursor("kr_equities") == 2
        runnable = store.runnable_evidence("day_trading", NOW)
        assert tuple(item.evidence.evidence_id for item in runnable) == (pending_us.evidence_id,)


def test_v1_legacy_day_open_work_recovers_as_us_market_work(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    evidence = _evidence("day_trading", 1, "us_equities")
    legacy_work = ResearchAgentOpenWorkV1(
        work_id="actor-state.day_trading",
        cycle_id=CycleId("a" * 64),
        agent_family_id="day_trading",
        state=ResearchAgentOpenWorkState.OPEN,
        evidence_refs=("b" * 64,),
        next_wake_at=NOW + dt.timedelta(minutes=1),
        updated_at=NOW,
        source_evidence_id=evidence.evidence_id,
        failure_count=1,
    )
    with sqlite3.connect(path) as connection:
        for statement in RESEARCH_AGENT_CYCLE_SCHEMA_V1:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO open_work(open_work_id,agent_family_id,state,payload_json) VALUES(?,?,?,?)",
            (
                legacy_work.work_id,
                legacy_work.agent_family_id,
                legacy_work.state,
                json.dumps(
                    legacy_work.model_dump(mode="json"),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
    os.chmod(path, 0o600)

    with ResearchAgentCycleStore(path) as store:
        recovered = store.open_work("day_trading")[0]
        assert retry_evidence(recovered, NOW + dt.timedelta(minutes=2)).market_id == "us_equities"


def test_latest_day_cycles_keep_one_latest_cycle_per_market(tmp_path: Path) -> None:
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        for evidence in (
            _evidence("day_trading", 1, "us_equities"),
            _evidence("day_trading", 2, "kr_equities"),
            _evidence("day_trading", 3, "us_equities"),
        ):
            store.append_evidence(evidence)
        for market_id in ("us_equities", "kr_equities", "us_equities"):
            stored = next(
                item for item in store.runnable_evidence("day_trading", NOW) if item.evidence.market_id == market_id
            )
            cycle = store.start_cycle(stored, NOW)
            store.finish_cycle(cycle, _result(cycle.cycle_id, "day_trading", market_id))

        latest = tuple(cycle for cycle in store.latest_cycles() if cycle.agent_family_id == "day_trading")

    assert tuple(cycle.market_id for cycle in latest) == ("us_equities", "kr_equities")
    assert latest[0].evidence_sequence > latest[1].evidence_sequence
