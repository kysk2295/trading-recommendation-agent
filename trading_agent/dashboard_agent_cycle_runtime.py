from __future__ import annotations

import datetime as dt
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.dashboard_models_v2 import ResearchAgentCycleViewV2
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentOpenWorkState,
)
from trading_agent.research_agent_cycle_schema import RESEARCH_AGENT_CYCLE_SCHEMA_VERSION
from trading_agent.research_agent_cycle_store_codec import (
    latest_cycles_from_rows,
    open_work_from_payload,
    result_from_payload,
    stored_evidence,
)
from trading_agent.research_agent_cycle_store_support import (
    InvalidResearchAgentCycleStoreError,
)


class InvalidAgentCycleRuntimeError(RuntimeError):
    pass


AgentRuntimeState = Literal["running", "armed", "idle", "failed", "unavailable"]


@dataclass(frozen=True, slots=True)
class AgentRuntimeObservation:
    family: AgentFamilyId
    state: AgentRuntimeState
    observed_at: dt.datetime


def read_cycle_runtime_observations(path: Path) -> tuple[AgentRuntimeObservation, ...]:
    source = path.expanduser().absolute()
    _require_private_database(source)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
            _ = connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA user_version").fetchone() != (RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,):
                raise InvalidAgentCycleRuntimeError
            cycles = latest_cycles_from_rows(
                connection.execute(
                    "SELECT agent_family_id,payload_json FROM cycles ORDER BY evidence_sequence DESC"
                ).fetchall()
            )
            open_work = tuple(
                open_work_from_payload(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM open_work WHERE state=? ORDER BY open_work_id",
                    (ResearchAgentOpenWorkState.OPEN,),
                ).fetchall()
            )
    except (InvalidResearchAgentCycleStoreError, OSError, sqlite3.Error, ValueError):
        raise InvalidAgentCycleRuntimeError from None
    open_families = frozenset[AgentFamilyId](item.agent_family_id for item in open_work)
    return tuple(_observation(cycle, open_families) for cycle in cycles)


def read_research_board_cycles(path: Path) -> tuple[ResearchAgentCycleViewV2, ...]:
    source = path.expanduser().absolute()
    _require_private_database(source)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
            _ = connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA user_version").fetchone() != (RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,):
                raise InvalidAgentCycleRuntimeError
            cycles = latest_cycles_from_rows(
                connection.execute(
                    "SELECT agent_family_id,payload_json FROM cycles ORDER BY evidence_sequence DESC"
                ).fetchall()
            )
            evidence_by_sequence = {
                item.sequence: item.evidence
                for item in (
                    stored_evidence(row)
                    for row in connection.execute(
                        "SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence"
                    ).fetchall()
                )
            }
            results = {
                str(result.cycle_id): result
                for result in (
                    result_from_payload(row[0])
                    for row in connection.execute("SELECT payload_json FROM results").fetchall()
                )
            }
    except (InvalidResearchAgentCycleStoreError, OSError, sqlite3.Error, ValueError):
        raise InvalidAgentCycleRuntimeError from None
    cycle_by_family = {cycle.agent_family_id: cycle for cycle in cycles}
    rows: list[ResearchAgentCycleViewV2] = []
    for family in PRIMARY_AGENT_FAMILIES:
        cycle = cycle_by_family.get(family)
        if cycle is None:
            rows.append(_unavailable_board_row(family))
            continue
        evidence = evidence_by_sequence.get(cycle.evidence_sequence)
        if evidence is None or evidence.evidence_id != cycle.evidence_id:
            raise InvalidAgentCycleRuntimeError
        result = results.get(str(cycle.cycle_id))
        rows.append(
            ResearchAgentCycleViewV2(
                agent_family_id=family,
                cycle_state=cycle.state.value,
                result_status=None if result is None else result.status.value,
                input_source=evidence.source_key,
                decision_kind=None if result is None or result.decision_kind is None else result.decision_kind.value,
                result_summary=None if result is None else result.summary[:160],
                artifact_count=0 if result is None else len(result.artifact_refs),
                observed_at=cycle.terminal_at or cycle.started_at,
                next_wake_kind=None if result is None else result.next_wake_kind.value,
                next_wake_at=None if result is None else result.next_wake_at,
            )
        )
    return tuple(rows)


def _unavailable_board_row(family: AgentFamilyId) -> ResearchAgentCycleViewV2:
    return ResearchAgentCycleViewV2(
        agent_family_id=family,
        cycle_state="unavailable",
        result_status=None,
        input_source=None,
        decision_kind=None,
        result_summary=None,
        artifact_count=0,
        observed_at=None,
        next_wake_kind=None,
        next_wake_at=None,
    )


def _require_private_database(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        raise InvalidAgentCycleRuntimeError
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidAgentCycleRuntimeError


def _observation(
    cycle: ResearchAgentCycleV1,
    open_families: frozenset[AgentFamilyId],
) -> AgentRuntimeObservation:
    match cycle.state:
        case ResearchAgentCycleState.STARTED:
            state = "running"
        case ResearchAgentCycleState.INTERRUPTED | ResearchAgentCycleState.FAILED | ResearchAgentCycleState.BLOCKED:
            state = "failed"
        case ResearchAgentCycleState.COMPLETED:
            state = "armed" if cycle.agent_family_id in open_families else "idle"
        case unreachable:
            assert_never(unreachable)
    return AgentRuntimeObservation(
        family=cycle.agent_family_id,
        state=state,
        observed_at=cycle.terminal_at or cycle.started_at,
    )


__all__ = (
    "AgentRuntimeObservation",
    "AgentRuntimeState",
    "InvalidAgentCycleRuntimeError",
    "read_cycle_runtime_observations",
    "read_research_board_cycles",
)
