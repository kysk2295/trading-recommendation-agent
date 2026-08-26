from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent import _autonomous_supervisor_steps as steps
from trading_agent._autonomous_supervisor_steps import SourceAdmissionPayload, canonical_json, payload_json, plain_step
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_due_adapter import AutonomousSupervisorProjection
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousSupervisorTickResult,
    AutonomousTaskId,
    AutonomousTaskState,
    autonomous_task_id,
)
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore

_SOURCE_KEY: Final = "browser_research_agenda.episode"
_SUBJECT_REF: Final = "browser_research_agenda.kr_equities"
_AGENT_VERSION: Final = "browser-research-agenda-v1"
_GOAL: Final = (
    "Continuously discover and test Korean-market theme and supply-demand hypotheses from social, community, "
    "news, and web sources; distinguish independent corroboration from reposting; use durable memory; and choose "
    "a durable timed wait when no useful action remains."
)
_GOAL_DIGEST: Final = hashlib.sha256(_GOAL.encode()).hexdigest()
_PLAN: Final = (
    "form and test Korean-market hypotheses",
    "retain corroborated evidence and useful memory",
    "wait durably when no useful action remains",
)
_ENSURE_LOCK: Final = threading.RLock()
_PERIODIC_REVIEW: Final = dt.timedelta(minutes=10)


class InvalidBrowserResearchAgendaError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class BrowserResearchAgendaEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    episode_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_id: AutonomousTaskId = Field(pattern=r"^[a-f0-9]{64}$")
    predecessor_task_id: AutonomousTaskId | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    root_evidence_id: EvidenceId = Field(pattern=r"^[a-f0-9]{64}$")
    opened_at: AwareDatetime
    goal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class BrowserResearchAgendaEpisodeStore:
    cycles: ResearchAgentCycleStore

    def all(self) -> tuple[BrowserResearchAgendaEpisode, ...]:
        return tuple(
            _episode_from_evidence(evidence)
            for evidence in self.cycles.all_evidence()
            if evidence.source_key == _SOURCE_KEY
        )

    def get_by_task(self, task_id: AutonomousTaskId) -> BrowserResearchAgendaEpisode | None:
        return next((episode for episode in reversed(self.all()) if episode.task_id == task_id), None)

    def latest(self) -> BrowserResearchAgendaEpisode | None:
        episodes = self.all()
        return None if not episodes else episodes[-1]


@dataclass(frozen=True, slots=True)
class ContinuousBrowserResearchSupervisor:
    supervisor: AutonomousSupervisorAdapter
    cycles: ResearchAgentCycleStore
    owns_cycles: bool = True

    @property
    def episodes(self) -> BrowserResearchAgendaEpisodeStore:
        return BrowserResearchAgendaEpisodeStore(self.cycles)

    def close(self) -> None:
        try:
            self.supervisor.close()
        finally:
            if self.owns_cycles:
                self.cycles.close()

    def tick(self, evidence: ResearchAgentEvidenceV1, now: dt.datetime) -> AutonomousSupervisorTickResult:
        return self.supervisor.tick(evidence, now)

    def admit_evidence(self, evidence: ResearchAgentEvidenceV1, now: dt.datetime) -> AutonomousResearchTask:
        return self.supervisor.admit_evidence(evidence, now)

    def admit_matching_evidence(self, evidence: ResearchAgentEvidenceV1, now: dt.datetime) -> bool:
        return self.supervisor.admit_matching_evidence(evidence, now)

    def admitted_evidence_ids(self) -> frozenset[EvidenceId]:
        return self.supervisor.admitted_evidence_ids()

    def recoverable_projections(self) -> tuple[AutonomousSupervisorProjection, ...]:
        return self.supervisor.recoverable_projections()

    def projection_for_result(self, result: AutonomousSupervisorTickResult) -> AutonomousSupervisorProjection:
        return self.supervisor.projection_for_result(result)

    def project_tick(
        self,
        cycle: ResearchAgentCycleV1,
        result: AutonomousSupervisorTickResult,
        now: dt.datetime,
    ) -> ResearchAgentResultV1:
        return self.supervisor.project_tick(cycle, result, now)

    def ensure_open(self, now: dt.datetime) -> AutonomousResearchTask:
        if now.tzinfo is None or now.utcoffset() is None:
            raise InvalidBrowserResearchAgendaError(reason="agenda_time_invalid")
        with _ENSURE_LOCK:
            episode = self.episodes.latest()
            if episode is None:
                episode = _episode(None, now)
            task = self.supervisor.runtime.tasks.reader().task(episode.task_id)
            if task is not None and task.state not in {AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED}:
                return self._schedule_event_wait(task, now)
            if task is not None:
                episode = _episode(task, now)
            evidence = _evidence(episode)
            _ = self.cycles.append_evidence(evidence)
            return self._admit(episode, evidence, now)

    def run_due(self, now: dt.datetime) -> tuple[AutonomousSupervisorProjection, ...]:
        _ = self.ensure_open(now)
        projections = self.supervisor.run_due(now)
        task = self.ensure_open(now)
        return tuple(
            self.supervisor.projection_for_result(
                item.result.model_copy(update={"next_wake_at": task.next_wake_at, "next_wake_event": None})
            )
            if item.result.task_id == task.task_id and item.result.next_wake_event is not None
            else item
            for item in projections
        )

    def _schedule_event_wait(self, task: AutonomousResearchTask, now: dt.datetime) -> AutonomousResearchTask:
        if task.state is not AutonomousTaskState.WAITING_EVENT:
            return task
        wake = max(task.updated_at + _PERIODIC_REVIEW, now + dt.timedelta(seconds=1))
        step = plain_step(
            task,
            len(self.supervisor.runtime.tasks.reader().steps(task.task_id)) + 1,
            now,
            AutonomousTaskState.WAITING_TIME,
            payload_json(steps.WaitPayload(cause="periodic", resume_condition="Resume continuous review.")),
            task.source_evidence_ids,
            task.evidence_refs,
            wake=wake,
        )
        with self.supervisor.runtime.tasks.writer() as writer:
            _ = writer.append_step(step)
        return self.supervisor.runtime.tasks.reader().task(task.task_id) or task

    def _admit(
        self,
        episode: BrowserResearchAgendaEpisode,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousResearchTask:
        with self.supervisor.runtime.tasks.admission_writer() as writer:
            existing = writer.task(episode.task_id)
            if existing is None:
                task = _task(episode, evidence, now)
                step = plain_step(
                    task,
                    1,
                    now,
                    AutonomousTaskState.QUEUED,
                    payload_json(
                        SourceAdmissionPayload(
                            evidence_id=evidence.evidence_id,
                            evidence_json=canonical_json(evidence.model_dump(mode="json")),
                        )
                    ),
                    task.source_evidence_ids,
                    task.evidence_refs,
                )
                _ = writer.create_task_with_initial_step(task, step)
        return self.supervisor.admit_evidence(evidence, now)


def _episode(
    predecessor: AutonomousResearchTask | None,
    opened_at: dt.datetime,
) -> BrowserResearchAgendaEpisode:
    predecessor_task_id = None if predecessor is None else predecessor.task_id
    material = f"{predecessor_task_id or 'initial'}:{_GOAL_DIGEST}:browser-research-agenda-v1"
    episode_id = hashlib.sha256(material.encode()).hexdigest()
    root_evidence_id = EvidenceId(hashlib.sha256(f"{episode_id}:evidence-v1".encode()).hexdigest())
    return BrowserResearchAgendaEpisode(
        episode_id=episode_id,
        task_id=autonomous_task_id("market_context", "kr_equities", root_evidence_id),
        predecessor_task_id=predecessor_task_id,
        root_evidence_id=root_evidence_id,
        opened_at=opened_at.astimezone(dt.UTC),
        goal_digest=_GOAL_DIGEST,
    )


def _evidence(episode: BrowserResearchAgendaEpisode) -> ResearchAgentEvidenceV1:
    payload = json.dumps(episode.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    predecessor_refs = () if episode.predecessor_task_id is None else (str(episode.predecessor_task_id),)
    return ResearchAgentEvidenceV1(
        evidence_id=episode.root_evidence_id,
        agent_family_id="market_context",
        trigger_kind=ResearchAgentTriggerKind.OPEN_WORK,
        source_key=_SOURCE_KEY,
        evidence_refs=tuple(sorted((_GOAL_DIGEST, *predecessor_refs))),
        observed_at=episode.opened_at,
        available_at=episode.opened_at,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        market_id="kr_equities",
        bounded_payload_json=payload,
        subject_refs=(_SUBJECT_REF,),
    )


def _task(
    episode: BrowserResearchAgendaEpisode,
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> AutonomousResearchTask:
    refs = tuple(sorted(set(evidence.evidence_refs) | {evidence.payload_sha256}))
    return AutonomousResearchTask(
        task_id=episode.task_id,
        goal=_GOAL,
        owner_role=AutonomousAgentRole.MARKET_OBSERVER,
        agent_family_id="market_context",
        market_scope="kr_equities",
        state=AutonomousTaskState.QUEUED,
        priority=60,
        root_source_evidence_id=evidence.evidence_id,
        source_evidence_ids=(evidence.evidence_id,),
        evidence_refs=refs,
        subject_refs=(_SUBJECT_REF,),
        current_plan=tuple(sorted(_PLAN)),
        agent_version=_AGENT_VERSION,
        created_at=now.astimezone(dt.UTC),
        updated_at=now.astimezone(dt.UTC),
    )


def _episode_from_evidence(evidence: ResearchAgentEvidenceV1) -> BrowserResearchAgendaEpisode:
    try:
        episode = BrowserResearchAgendaEpisode.model_validate_json(evidence.bounded_payload_json or "")
    except ValidationError:
        raise InvalidBrowserResearchAgendaError(reason="agenda_episode_payload_invalid") from None
    if (
        episode.root_evidence_id != evidence.evidence_id
        or episode.task_id != autonomous_task_id("market_context", "kr_equities", episode.root_evidence_id)
        or episode.goal_digest != _GOAL_DIGEST
    ):
        raise InvalidBrowserResearchAgendaError(reason="agenda_episode_identity_invalid")
    return episode


__all__ = (
    "BrowserResearchAgendaEpisode",
    "BrowserResearchAgendaEpisodeStore",
    "ContinuousBrowserResearchSupervisor",
    "InvalidBrowserResearchAgendaError",
)
