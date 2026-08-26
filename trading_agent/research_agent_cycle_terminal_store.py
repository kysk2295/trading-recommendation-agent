from __future__ import annotations

from dataclasses import dataclass

from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultV1,
)
from trading_agent.research_agent_cycle_store_codec import (
    append_cycle_event,
    canonical_cycle_json,
    cycle_from_payload,
    cycle_state_for_result,
    open_work_from_payload,
    update_cycle,
)
from trading_agent.research_agent_cycle_store_support import (
    InvalidResearchAgentCycleStoreError,
    ResearchAgentCycleDatabaseLease,
)


@dataclass(frozen=True, slots=True)
class CycleTerminalization:
    cycle: ResearchAgentCycleV1
    result: ResearchAgentResultV1
    open_work: ResearchAgentOpenWorkV1 | None = None


def terminalize_cycle(database: ResearchAgentCycleDatabaseLease, mutation: CycleTerminalization) -> None:
    cycle = mutation.cycle
    result = mutation.result
    work = mutation.open_work
    if result.cycle_id != cycle.cycle_id or result.agent_family_id != cycle.agent_family_id:
        raise InvalidResearchAgentCycleStoreError(reason="result_cycle_identity_mismatch")
    if work is not None and (
        work.agent_family_id != cycle.agent_family_id or work.state is not ResearchAgentOpenWorkState.TERMINAL
    ):
        raise InvalidResearchAgentCycleStoreError(reason="terminal_open_work_identity_mismatch")
    terminal = ResearchAgentCycleV1.model_validate(
        cycle.model_dump(mode="python")
        | {
            "state": cycle_state_for_result(result.status),
            "terminal_at": result.occurred_at,
            "result_id": result.result_id,
        }
    )
    result_payload = canonical_cycle_json(result)
    with database.writer() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload_json FROM cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
        existing = connection.execute(
            "SELECT payload_json FROM results WHERE cycle_id=?",
            (cycle.cycle_id,),
        ).fetchone()
        existing_work = (
            None
            if work is None
            else connection.execute(
                "SELECT payload_json FROM open_work WHERE open_work_id=?",
                (work.work_id,),
            ).fetchone()
        )
        if existing is not None:
            connection.rollback()
            work_matches = work is None or (
                existing_work is not None and open_work_from_payload(existing_work[0]) == work
            )
            if (
                row is not None
                and existing[0] == result_payload
                and cycle_from_payload(row[0]) == terminal
                and work_matches
            ):
                return
            raise InvalidResearchAgentCycleStoreError(reason="result_identity_conflict")
        if row is None or cycle_from_payload(row[0]) != cycle:
            connection.rollback()
            raise InvalidResearchAgentCycleStoreError(reason="started_cycle_missing")
        _ = connection.execute(
            "INSERT INTO results(result_id,cycle_id,payload_json) VALUES(?,?,?)",
            (result.result_id, cycle.cycle_id, result_payload),
        )
        update_cycle(connection, terminal)
        append_cycle_event(connection, terminal, result.occurred_at)
        _advance_cursor(connection, terminal)
        if work is not None:
            _ = connection.execute(
                """INSERT INTO open_work(open_work_id,agent_family_id,state,payload_json) VALUES(?,?,?,?)
                ON CONFLICT(open_work_id) DO UPDATE SET state=excluded.state,payload_json=excluded.payload_json
                WHERE open_work.agent_family_id=excluded.agent_family_id""",
                (work.work_id, work.agent_family_id, work.state, canonical_cycle_json(work)),
            )
        connection.commit()


def _advance_cursor(connection, cycle: ResearchAgentCycleV1) -> None:
    if cycle.agent_family_id == "day_trading":
        _ = connection.execute(
            """INSERT INTO day_cursors(agent_family_id,market_id,evidence_sequence)
            VALUES(?,?,?) ON CONFLICT(agent_family_id,market_id) DO UPDATE
            SET evidence_sequence=MAX(evidence_sequence,excluded.evidence_sequence)""",
            (cycle.agent_family_id, cycle.market_id, cycle.evidence_sequence),
        )
        return
    _ = connection.execute(
        """INSERT INTO cursors(agent_family_id,evidence_sequence) VALUES(?,?)
        ON CONFLICT(agent_family_id) DO UPDATE
        SET evidence_sequence=MAX(evidence_sequence,excluded.evidence_sequence)""",
        (cycle.agent_family_id, cycle.evidence_sequence),
    )


__all__ = ("CycleTerminalization", "terminalize_cycle")
