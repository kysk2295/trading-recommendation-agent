from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    research_agent_action_id,
)
from trading_agent.research_agent_cycle_store_codec import (
    StoredResearchAgentEvidence,
    append_cycle_event,
    cycle_from_payload,
    insert_cycle,
    require_same_cycle_identity,
    update_cycle,
)
from trading_agent.research_agent_cycle_store_support import ResearchAgentCycleDatabaseLease
from trading_agent.research_agent_supervisor_cycle_identity import research_agent_supervisor_cycle_id


@dataclass(frozen=True, slots=True)
class SupervisorCycleStart:
    stored: StoredResearchAgentEvidence
    started_at: dt.datetime
    checkpoint_ref: str


def start_supervisor_cycle(
    database: ResearchAgentCycleDatabaseLease,
    request: SupervisorCycleStart,
) -> ResearchAgentCycleV1:
    stored = request.stored
    with database.reader() as connection:
        if stored.evidence.agent_family_id == "day_trading":
            row = connection.execute(
                "SELECT evidence_sequence FROM day_cursors WHERE agent_family_id=? AND market_id=?",
                ("day_trading", stored.evidence.market_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT evidence_sequence FROM cursors WHERE agent_family_id=?",
                (stored.evidence.agent_family_id,),
            ).fetchone()
    cursor_before = 0 if row is None else int(row[0])
    cycle_id = research_agent_supervisor_cycle_id(stored.evidence, request.checkpoint_ref)
    candidate = ResearchAgentCycleV1(
        cycle_id=cycle_id,
        evidence_id=stored.evidence.evidence_id,
        action_request_id=research_agent_action_id(cycle_id),
        agent_family_id=stored.evidence.agent_family_id,
        market_id=stored.evidence.market_id,
        evidence_sequence=stored.sequence,
        cursor_before=cursor_before,
        state=ResearchAgentCycleState.STARTED,
        started_at=request.started_at,
    )
    with database.writer() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT payload_json FROM cycles WHERE cycle_id=?",
            (cycle_id,),
        ).fetchone()
        if row is None:
            insert_cycle(connection, candidate)
            connection.commit()
            return candidate
        existing = cycle_from_payload(row[0])
        require_same_cycle_identity(existing, candidate)
        if existing.state is not ResearchAgentCycleState.INTERRUPTED:
            connection.rollback()
            return existing
        replay = existing.model_copy(
            update={"state": ResearchAgentCycleState.STARTED, "started_at": request.started_at, "terminal_at": None}
        )
        update_cycle(connection, replay)
        append_cycle_event(connection, replay, request.started_at)
        connection.commit()
        return replay


__all__ = ("SupervisorCycleStart", "start_supervisor_cycle")
