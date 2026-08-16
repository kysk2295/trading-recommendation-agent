from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from typing import assert_never

from pydantic import ValidationError

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
)
from trading_agent.research_agent_cycle_store_support import InvalidResearchAgentCycleStoreError


@dataclass(frozen=True, slots=True)
class StoredResearchAgentEvidence:
    sequence: int
    evidence: ResearchAgentEvidenceV1


@dataclass(frozen=True, slots=True)
class StoredResearchAgentCycleEvent:
    sequence: int
    state: ResearchAgentCycleState
    occurred_at: dt.datetime
    cycle: ResearchAgentCycleV1


def canonical_cycle_json(
    item: ResearchAgentEvidenceV1 | ResearchAgentCycleV1 | ResearchAgentResultV1 | ResearchAgentOpenWorkV1,
) -> str:
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def stored_evidence(row: tuple[int, str, str, str]) -> StoredResearchAgentEvidence:
    sequence, evidence_id, family, payload = row
    try:
        evidence = ResearchAgentEvidenceV1.model_validate_json(payload)
    except ValidationError:
        raise InvalidResearchAgentCycleStoreError(reason="stored_evidence_invalid") from None
    if evidence.evidence_id != evidence_id or evidence.agent_family_id != family:
        raise InvalidResearchAgentCycleStoreError(reason="stored_evidence_identity_invalid")
    return StoredResearchAgentEvidence(sequence=sequence, evidence=evidence)


def cycle_from_payload(payload: str) -> ResearchAgentCycleV1:
    try:
        return ResearchAgentCycleV1.model_validate_json(payload)
    except ValidationError:
        raise InvalidResearchAgentCycleStoreError(reason="stored_cycle_invalid") from None


def result_from_payload(payload: str) -> ResearchAgentResultV1:
    try:
        return ResearchAgentResultV1.model_validate_persisted_json(payload)
    except ValidationError:
        raise InvalidResearchAgentCycleStoreError(reason="stored_result_invalid") from None


def open_work_from_payload(payload: str) -> ResearchAgentOpenWorkV1:
    try:
        return ResearchAgentOpenWorkV1.model_validate_json(payload)
    except ValidationError:
        raise InvalidResearchAgentCycleStoreError(reason="stored_open_work_invalid") from None


def stored_cycle_event(row: tuple[int, str, str, str]) -> StoredResearchAgentCycleEvent:
    sequence, raw_state, raw_occurred_at, payload = row
    cycle = cycle_from_payload(payload)
    try:
        state = ResearchAgentCycleState(raw_state)
        occurred_at = dt.datetime.fromisoformat(raw_occurred_at)
    except ValueError:
        raise InvalidResearchAgentCycleStoreError(reason="stored_cycle_event_invalid") from None
    if cycle.state is not state or occurred_at.tzinfo is None:
        raise InvalidResearchAgentCycleStoreError(reason="stored_cycle_event_identity_invalid")
    return StoredResearchAgentCycleEvent(sequence, state, occurred_at, cycle)


def require_same_cycle_identity(existing: ResearchAgentCycleV1, candidate: ResearchAgentCycleV1) -> None:
    if (
        existing.cycle_id != candidate.cycle_id
        or existing.evidence_id != candidate.evidence_id
        or existing.action_request_id != candidate.action_request_id
        or existing.agent_family_id != candidate.agent_family_id
        or existing.market_id != candidate.market_id
        or existing.evidence_sequence != candidate.evidence_sequence
        or existing.cursor_before != candidate.cursor_before
    ):
        raise InvalidResearchAgentCycleStoreError(reason="cycle_identity_conflict")


def latest_cycles_from_rows(rows: list[tuple[str, str]]) -> tuple[ResearchAgentCycleV1, ...]:
    remaining = set(PRIMARY_AGENT_FAMILIES)
    latest: list[ResearchAgentCycleV1] = []
    for family, payload in rows:
        if family in remaining:
            cycle = cycle_from_payload(payload)
            latest.append(cycle)
            remaining.remove(cycle.agent_family_id)
    return tuple(latest)


def cycle_state_for_result(status: ResearchAgentResultStatus) -> ResearchAgentCycleState:
    match status:
        case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
            return ResearchAgentCycleState.COMPLETED
        case ResearchAgentResultStatus.FAILED:
            return ResearchAgentCycleState.FAILED
        case ResearchAgentResultStatus.BLOCKED:
            return ResearchAgentCycleState.BLOCKED
        case unreachable:
            assert_never(unreachable)


def insert_cycle(connection: sqlite3.Connection, cycle: ResearchAgentCycleV1) -> None:
    _ = connection.execute(
        """INSERT INTO cycles(cycle_id,agent_family_id,evidence_sequence,action_request_id,state,
        started_at,terminal_at,payload_json) VALUES(?,?,?,?,?,?,?,?)""",
        (
            cycle.cycle_id,
            cycle.agent_family_id,
            cycle.evidence_sequence,
            cycle.action_request_id,
            cycle.state,
            cycle.started_at.isoformat(),
            None,
            canonical_cycle_json(cycle),
        ),
    )
    append_cycle_event(connection, cycle, cycle.started_at)


def update_cycle(connection: sqlite3.Connection, cycle: ResearchAgentCycleV1) -> None:
    _ = connection.execute(
        "UPDATE cycles SET state=?,started_at=?,terminal_at=?,payload_json=? WHERE cycle_id=?",
        (
            cycle.state,
            cycle.started_at.isoformat(),
            None if cycle.terminal_at is None else cycle.terminal_at.isoformat(),
            canonical_cycle_json(cycle),
            cycle.cycle_id,
        ),
    )


def append_cycle_event(connection: sqlite3.Connection, cycle: ResearchAgentCycleV1, occurred_at: dt.datetime) -> None:
    _ = connection.execute(
        "INSERT INTO cycle_events(cycle_id,state,occurred_at,payload_json) VALUES(?,?,?,?)",
        (cycle.cycle_id, cycle.state, occurred_at.isoformat(), canonical_cycle_json(cycle)),
    )


__all__ = (
    "StoredResearchAgentCycleEvent",
    "StoredResearchAgentEvidence",
    "append_cycle_event",
    "canonical_cycle_json",
    "cycle_from_payload",
    "cycle_state_for_result",
    "insert_cycle",
    "latest_cycles_from_rows",
    "open_work_from_payload",
    "require_same_cycle_identity",
    "result_from_payload",
    "stored_cycle_event",
    "stored_evidence",
    "update_cycle",
)
