from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Final, Literal, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousTaskId,
    AutonomousTaskState,
    autonomous_task_id,
)
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    ResearchAgentEvidenceV1,
    ResearchAgentTriggerKind,
)

SOURCE_KEY: Final = "browser_research_agenda.episode"
SUBJECT_REF: Final = "browser_research_agenda.kr_equities"
_V1_AGENT_VERSION: Final = "browser-research-agenda-v1"
_V1_GOAL: Final = (
    "Continuously discover and test Korean-market theme and supply-demand hypotheses from social, community, "
    "news, and web sources; distinguish independent corroboration from reposting; use durable memory; and choose "
    "a durable timed wait when no useful action remains."
)
_V1_PLAN: Final = (
    "form and test Korean-market hypotheses",
    "retain corroborated evidence and useful memory",
    "wait durably when no useful action remains",
)
_V2_AGENT_VERSION: Final = "browser-research-agenda-v2"
_V2_GOAL: Final = (
    "Continuously research Korean equities from durable social evidence; normalize independent sources; "
    "corroborate numerical market truth with current KIS observations; own explicit recommendation or no-trade "
    "decisions and internal virtual positions; retain lineage and durable waits without real trading authority."
)
_V2_PLAN: Final = (
    "own bounded Korean-market research and decisions",
    "retain evidence lineage and virtual-position responsibility",
    "wait durably when no useful action remains",
)
_V1_GOAL_DIGEST: Final = hashlib.sha256(_V1_GOAL.encode()).hexdigest()
_V2_GOAL_DIGEST: Final = hashlib.sha256(_V2_GOAL.encode()).hexdigest()


class InvalidBrowserResearchAgendaContractError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: Literal["agenda_episode_payload_invalid", "agenda_episode_identity_invalid"]) -> None:
        self.reason = reason
        super().__init__(reason)


class BrowserResearchAgendaEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    episode_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_id: AutonomousTaskId = Field(pattern=r"^[a-f0-9]{64}$")
    predecessor_task_id: AutonomousTaskId | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    root_evidence_id: EvidenceId = Field(pattern=r"^[a-f0-9]{64}$")
    opened_at: AwareDatetime
    goal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    agenda_version: Literal[1, 2] = 1


def create_episode(
    predecessor: AutonomousResearchTask | None,
    opened_at: dt.datetime,
    agenda_version: Literal[1, 2],
) -> BrowserResearchAgendaEpisode:
    predecessor_id = None if predecessor is None else predecessor.task_id
    digest, agent_version, _goal, _plan = agenda_contract(agenda_version)
    episode_id = hashlib.sha256(f"{predecessor_id or 'initial'}:{digest}:{agent_version}".encode()).hexdigest()
    evidence_id = EvidenceId(hashlib.sha256(f"{episode_id}:evidence-v{agenda_version}".encode()).hexdigest())
    return BrowserResearchAgendaEpisode(
        episode_id=episode_id,
        task_id=autonomous_task_id("market_context", "kr_equities", evidence_id),
        predecessor_task_id=predecessor_id,
        root_evidence_id=evidence_id,
        opened_at=opened_at.astimezone(dt.UTC),
        goal_digest=digest,
        agenda_version=agenda_version,
    )


def evidence_for_episode(episode: BrowserResearchAgendaEpisode) -> ResearchAgentEvidenceV1:
    payload = json.dumps(
        episode.model_dump(mode="json", exclude={"agenda_version"} if episode.agenda_version == 1 else set()),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    predecessor_refs = () if episode.predecessor_task_id is None else (str(episode.predecessor_task_id),)
    return ResearchAgentEvidenceV1(
        evidence_id=episode.root_evidence_id,
        agent_family_id="market_context",
        trigger_kind=ResearchAgentTriggerKind.OPEN_WORK,
        source_key=SOURCE_KEY,
        evidence_refs=tuple(sorted((episode.goal_digest, *predecessor_refs))),
        observed_at=episode.opened_at,
        available_at=episode.opened_at,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        market_id="kr_equities",
        bounded_payload_json=payload,
        subject_refs=(SUBJECT_REF,),
    )


def task_for_episode(
    episode: BrowserResearchAgendaEpisode,
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> AutonomousResearchTask:
    _digest, agent_version, goal, plan = agenda_contract(episode.agenda_version)
    refs = tuple(sorted(set(evidence.evidence_refs) | {evidence.payload_sha256}))
    return AutonomousResearchTask(
        task_id=episode.task_id,
        goal=goal,
        owner_role=AutonomousAgentRole.MARKET_OBSERVER,
        agent_family_id="market_context",
        market_scope="kr_equities",
        state=AutonomousTaskState.QUEUED,
        priority=60,
        root_source_evidence_id=evidence.evidence_id,
        source_evidence_ids=(evidence.evidence_id,),
        evidence_refs=refs,
        subject_refs=(SUBJECT_REF,),
        current_plan=tuple(sorted(plan)),
        agent_version=agent_version,
        created_at=now.astimezone(dt.UTC),
        updated_at=now.astimezone(dt.UTC),
    )


def episode_from_evidence(evidence: ResearchAgentEvidenceV1) -> BrowserResearchAgendaEpisode:
    try:
        episode = BrowserResearchAgendaEpisode.model_validate_json(evidence.bounded_payload_json or "")
    except ValidationError:
        raise InvalidBrowserResearchAgendaContractError(reason="agenda_episode_payload_invalid") from None
    if (
        episode.root_evidence_id != evidence.evidence_id
        or episode.task_id != autonomous_task_id("market_context", "kr_equities", episode.root_evidence_id)
        or episode.goal_digest != agenda_contract(episode.agenda_version)[0]
    ):
        raise InvalidBrowserResearchAgendaContractError(reason="agenda_episode_identity_invalid")
    return episode


def agenda_contract(version: Literal[1, 2]) -> tuple[str, str, str, tuple[str, ...]]:
    match version:
        case 1:
            return _V1_GOAL_DIGEST, _V1_AGENT_VERSION, _V1_GOAL, _V1_PLAN
        case 2:
            return _V2_GOAL_DIGEST, _V2_AGENT_VERSION, _V2_GOAL, _V2_PLAN
        case unreachable:
            assert_never(unreachable)
