from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import TracebackType
from typing import Self, assert_never, final

from trading_agent import research_agent_supervisor_cycle_store as supervisor_cycles
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_evidence_store import append_evidence
from trading_agent.research_agent_cycle_models import (
    CycleId,
    EvidenceId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_action_id,
    research_agent_cycle_id,
)
from trading_agent.research_agent_cycle_store_codec import (
    StoredResearchAgentCycleEvent,
    StoredResearchAgentEvidence,
    append_cycle_event,
    canonical_cycle_json,
    cycle_from_payload,
    insert_cycle,
    latest_cycles_from_rows,
    open_work_from_payload,
    require_same_cycle_identity,
    result_from_payload,
    stored_cycle_event,
    stored_evidence,
    update_cycle,
)
from trading_agent.research_agent_cycle_store_support import (
    InactiveResearchAgentCycleStoreError,
    InvalidResearchAgentCycleStoreError,
    ResearchAgentCycleDatabaseLease,
    ResearchAgentCycleWriterLeaseUnavailableError,
)
from trading_agent.research_agent_cycle_terminal_store import CycleTerminalization, terminalize_cycle


@final
class ResearchAgentCycleStore:
    __slots__ = ("_database", "path")

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        self._database = ResearchAgentCycleDatabaseLease(self.path)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: BaseException | type[BaseException] | TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self._database.close()

    def append_evidence(self, evidence: ResearchAgentEvidenceV1) -> bool:
        return append_evidence(self._database, evidence)

    def runnable_evidence(
        self,
        family: AgentFamilyId,
        now: dt.datetime,
    ) -> tuple[StoredResearchAgentEvidence, ...]:
        if family == "day_trading":
            with self._database.reader() as connection:
                rows = connection.execute(
                    """SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence
                    WHERE agent_family_id=? AND available_at<=? ORDER BY sequence""",
                    (family, now.astimezone(dt.UTC).isoformat()),
                ).fetchall()
            stored = tuple(stored_evidence(row) for row in rows)
            return tuple(
                item
                for item in stored
                if item.sequence > self.day_cursor(item.evidence.market_id)
            )
        with self._database.reader() as connection:
            rows = connection.execute(
                """SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence
                WHERE agent_family_id=?
                AND sequence > COALESCE((SELECT evidence_sequence FROM cursors WHERE agent_family_id=?),0)
                AND available_at<=? ORDER BY sequence""",
                (family, family, now.astimezone(dt.UTC).isoformat()),
            ).fetchall()
        return tuple(stored_evidence(row) for row in rows)

    def start_cycle(
        self,
        stored: StoredResearchAgentEvidence,
        started_at: dt.datetime,
        *,
        preserve_authority: bool = False,
    ) -> ResearchAgentCycleV1:
        cursor_before = (
            self.day_cursor(stored.evidence.market_id)
            if stored.evidence.agent_family_id == "day_trading"
            else self.cursor(stored.evidence.agent_family_id)
        )
        if stored.sequence <= cursor_before and not preserve_authority:
            raise InvalidResearchAgentCycleStoreError(reason="evidence_already_consumed")
        cycle_id = research_agent_cycle_id(stored.evidence, cursor_before=cursor_before)
        candidate = ResearchAgentCycleV1(
            cycle_id=cycle_id,
            evidence_id=stored.evidence.evidence_id,
            action_request_id=research_agent_action_id(cycle_id),
            agent_family_id=stored.evidence.agent_family_id,
            market_id=stored.evidence.market_id,
            evidence_sequence=stored.sequence,
            cursor_before=cursor_before,
            state=ResearchAgentCycleState.STARTED,
            started_at=started_at,
            terminal_at=None,
            result_id=None,
        )
        with self._database.writer() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_json FROM cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
            if row is None:
                insert_cycle(connection, candidate)
                connection.commit()
                return candidate
            existing = cycle_from_payload(row[0])
            require_same_cycle_identity(existing, candidate)
            if existing.state is not ResearchAgentCycleState.INTERRUPTED:
                connection.rollback()
                return existing
            replay = ResearchAgentCycleV1.model_validate(
                existing.model_dump(mode="python")
                | {"state": ResearchAgentCycleState.STARTED, "started_at": started_at, "terminal_at": None}
            )
            update_cycle(connection, replay)
            append_cycle_event(connection, replay, started_at)
            connection.commit()
            return replay

    def start_supervisor_cycle(self, request: supervisor_cycles.SupervisorCycleStart) -> ResearchAgentCycleV1:
        return supervisor_cycles.start_supervisor_cycle(self._database, request)

    def finish_cycle(self, cycle: ResearchAgentCycleV1, result: ResearchAgentResultV1) -> None:
        match result.status:
            case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
                terminalize_cycle(self._database, CycleTerminalization(cycle, result))
            case ResearchAgentResultStatus.FAILED | ResearchAgentResultStatus.BLOCKED:
                raise InvalidResearchAgentCycleStoreError(reason="finish_result_status_invalid")
            case unreachable:
                assert_never(unreachable)

    def fail_cycle(self, cycle: ResearchAgentCycleV1, result: ResearchAgentResultV1) -> None:
        match result.status:
            case ResearchAgentResultStatus.FAILED | ResearchAgentResultStatus.BLOCKED:
                terminalize_cycle(self._database, CycleTerminalization(cycle, result))
            case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
                raise InvalidResearchAgentCycleStoreError(reason="failure_result_status_invalid")
            case unreachable:
                assert_never(unreachable)

    def terminalize_supervisor_cycle(self, mutation: CycleTerminalization) -> None:
        terminalize_cycle(self._database, mutation)

    def recover_interrupted(self, recovered_at: dt.datetime) -> tuple[CycleId, ...]:
        recovered: list[CycleId] = []
        with self._database.writer() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT payload_json FROM cycles WHERE state=? ORDER BY evidence_sequence",
                (ResearchAgentCycleState.STARTED,),
            ).fetchall()
            for row in rows:
                cycle = cycle_from_payload(row[0])
                interrupted = ResearchAgentCycleV1.model_validate(
                    cycle.model_dump(mode="python")
                    | {"state": ResearchAgentCycleState.INTERRUPTED, "terminal_at": recovered_at}
                )
                update_cycle(connection, interrupted)
                append_cycle_event(connection, interrupted, recovered_at)
                recovered.append(interrupted.cycle_id)
            connection.commit()
        return tuple(recovered)

    def cursor(self, family: AgentFamilyId) -> int:
        if family == "day_trading":
            return max(
                self.day_cursor("us_equities"),
                self.day_cursor("kr_equities"),
                self.day_cursor("cross_market"),
                self.day_cursor("none"),
            )
        with self._database.reader() as connection:
            row = connection.execute(
                "SELECT evidence_sequence FROM cursors WHERE agent_family_id=?",
                (family,),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def day_cursor(self, market_id: str) -> int:
        if market_id not in {"us_equities", "kr_equities", "cross_market", "none"}:
            raise InvalidResearchAgentCycleStoreError(reason="day_cursor_market_invalid")
        with self._database.reader() as connection:
            row = connection.execute(
                "SELECT evidence_sequence FROM day_cursors WHERE agent_family_id=? AND market_id=?",
                ("day_trading", market_id),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def latest_cycles(self) -> tuple[ResearchAgentCycleV1, ...]:
        with self._database.reader() as connection:
            rows = connection.execute(
                "SELECT agent_family_id,payload_json FROM cycles ORDER BY evidence_sequence DESC"
            ).fetchall()
        return latest_cycles_from_rows(rows)

    def results(self) -> tuple[ResearchAgentResultV1, ...]:
        with self._database.reader() as connection:
            rows = connection.execute("SELECT payload_json FROM results ORDER BY rowid").fetchall()
        return tuple(result_from_payload(row[0]) for row in rows)

    def all_evidence(self) -> tuple[ResearchAgentEvidenceV1, ...]:
        with self._database.reader() as connection:
            rows = connection.execute(
                "SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence ORDER BY sequence"
            ).fetchall()
        return tuple(stored_evidence(row).evidence for row in rows)

    def evidence(self, evidence_id: EvidenceId) -> StoredResearchAgentEvidence | None:
        with self._database.reader() as connection:
            row = connection.execute(
                "SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
        return None if row is None else stored_evidence(row)

    def cycle_events(self, cycle_id: CycleId) -> tuple[StoredResearchAgentCycleEvent, ...]:
        with self._database.reader() as connection:
            rows = connection.execute(
                "SELECT event_sequence,state,occurred_at,payload_json FROM cycle_events "
                "WHERE cycle_id=? ORDER BY event_sequence",
                (cycle_id,),
            ).fetchall()
        return tuple(stored_cycle_event(row) for row in rows)

    def upsert_open_work(self, item: ResearchAgentOpenWorkV1) -> None:
        payload = canonical_cycle_json(item)
        with self._database.writer() as connection, connection:
            _ = connection.execute(
                """INSERT INTO open_work(open_work_id,agent_family_id,state,payload_json) VALUES(?,?,?,?)
                ON CONFLICT(open_work_id) DO UPDATE SET state=excluded.state,payload_json=excluded.payload_json
                WHERE open_work.agent_family_id=excluded.agent_family_id""",
                (item.work_id, item.agent_family_id, item.state, payload),
            )

    def open_work(self, family: AgentFamilyId) -> tuple[ResearchAgentOpenWorkV1, ...]:
        with self._database.reader() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM open_work WHERE agent_family_id=? ORDER BY open_work_id",
                (family,),
            ).fetchall()
        return tuple(open_work_from_payload(row[0]) for row in rows)

__all__ = (
    "InactiveResearchAgentCycleStoreError",
    "InvalidResearchAgentCycleStoreError",
    "ResearchAgentCycleStore",
    "ResearchAgentCycleWriterLeaseUnavailableError",
    "StoredResearchAgentCycleEvent",
    "StoredResearchAgentEvidence",
)
