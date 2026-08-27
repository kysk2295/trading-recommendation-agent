from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from typing import Final, Literal

from trading_agent import _autonomous_supervisor_steps as steps
from trading_agent._autonomous_supervisor_steps import SourceAdmissionPayload, canonical_json, payload_json, plain_step
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_due_adapter import AutonomousSupervisorProjection
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousSupervisorTickResult,
    AutonomousTaskId,
    AutonomousTaskState,
)
from trading_agent.browser_research_agenda_contract import (
    SOURCE_KEY,
    BrowserResearchAgendaEpisode,
    InvalidBrowserResearchAgendaContractError,
    create_episode,
    episode_from_evidence,
    evidence_for_episode,
    task_for_episode,
)
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultV1,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore

_ENSURE_LOCK: Final = threading.RLock()
_PERIODIC_REVIEW: Final = dt.timedelta(minutes=10)


class InvalidBrowserResearchAgendaError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class BrowserResearchAgendaEpisodeStore:
    cycles: ResearchAgentCycleStore

    def all(self) -> tuple[BrowserResearchAgendaEpisode, ...]:
        return tuple(
            _episode_from_evidence(evidence)
            for evidence in self.cycles.all_evidence()
            if evidence.source_key == SOURCE_KEY
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
    agenda_version: Literal[1, 2] = 1

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
                episode = create_episode(None, now, self.agenda_version)
            task = self.supervisor.runtime.tasks.reader().task(episode.task_id)
            if task is None:
                evidence = evidence_for_episode(episode)
                _ = self.cycles.append_evidence(evidence)
                task = self._admit(episode, evidence, now)
            if episode.agenda_version != self.agenda_version:
                episode = create_episode(task, now, self.agenda_version)
            elif task.state not in {
                AutonomousTaskState.COMPLETED,
                AutonomousTaskState.ABANDONED,
            }:
                return self._schedule_event_wait(task, now)
            else:
                episode = create_episode(task, now, self.agenda_version)
            evidence = evidence_for_episode(episode)
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
                task = task_for_episode(episode, evidence, now)
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


def _episode_from_evidence(evidence: ResearchAgentEvidenceV1) -> BrowserResearchAgendaEpisode:
    try:
        return episode_from_evidence(evidence)
    except InvalidBrowserResearchAgendaContractError as error:
        raise InvalidBrowserResearchAgendaError(reason=error.reason) from None


__all__ = (
    "BrowserResearchAgendaEpisode",
    "BrowserResearchAgendaEpisodeStore",
    "ContinuousBrowserResearchSupervisor",
    "InvalidBrowserResearchAgendaError",
)
