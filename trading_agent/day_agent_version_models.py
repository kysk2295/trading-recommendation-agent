from __future__ import annotations

import datetime as dt
import hashlib
import math
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_learning_report_models import DayDecisionStage
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_SHA256 = r"^[0-9a-f]{64}$"
_TASK_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$"


class DayAgentVersionStoreError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


class AgentDeploymentState(StrEnum):
    CHAMPION = "champion"
    SHADOW = "shadow"


class AgentChangeKind(StrEnum):
    MARKET_REGIME_POLICY = "market_regime_policy"
    THEME_SELECTION_POLICY = "theme_selection_policy"
    CATALYST_INTERPRETATION_POLICY = "catalyst_interpretation_policy"
    LEADER_RANKING_POLICY = "leader_ranking_policy"
    FLOW_INTERPRETATION_POLICY = "flow_interpretation_policy"
    ENTRY_POLICY = "entry_policy"
    EXIT_POLICY = "exit_policy"
    EXECUTION_REVIEW_POLICY = "execution_review_policy"


class AgentPromotionDecision(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"
    ROLLBACK = "rollback"


class DayAgentVersionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class AgentModelRoleBinding(DayAgentVersionModel):
    role: Literal["reasoning", "coding", "extraction"]
    model_id: str = Field(min_length=1, max_length=128)


class AgentVersionPayload(DayAgentVersionModel):
    model_role_bindings: tuple[AgentModelRoleBinding, ...] = Field(min_length=1)
    prompt_sha256: str = Field(pattern=_SHA256)
    tool_policy_sha256: str = Field(pattern=_SHA256)
    memory_retrieval_policy_sha256: str = Field(pattern=_SHA256)
    playbook_ids: tuple[str, ...]
    parent_version_id: str | None = Field(default=None, pattern=_SHA256)
    creation_evidence_ids: tuple[str, ...] = Field(min_length=1)
    deployment_state: AgentDeploymentState
    task_id: str = Field(pattern=_TASK_ID)
    created_at: AwareDatetime
    created_session_date: dt.date
    order_authority: Literal[False] = False
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        roles = tuple(item.role for item in self.model_role_bindings)
        if (
            roles != tuple(dict.fromkeys(roles))
            or self.playbook_ids != tuple(sorted(set(self.playbook_ids)))
            or any(len(item) != 64 for item in self.playbook_ids)
            or self.creation_evidence_ids != tuple(sorted(set(self.creation_evidence_ids)))
            or any(not item.strip() for item in self.creation_evidence_ids)
        ):
            raise DayAgentVersionStoreError("agent_version_payload_invalid")
        if self.deployment_state is AgentDeploymentState.CHAMPION and self.parent_version_id is not None:
            raise DayAgentVersionStoreError("initial_champion_parent_invalid")
        if self.deployment_state is AgentDeploymentState.SHADOW and self.parent_version_id is None:
            raise DayAgentVersionStoreError("challenger_parent_missing")
        return self


class AgentVersion(DayAgentVersionModel):
    version_id: str = Field(pattern=_SHA256)
    payload: AgentVersionPayload

    @property
    def model_role_bindings(self) -> tuple[AgentModelRoleBinding, ...]:
        return self.payload.model_role_bindings

    @property
    def prompt_sha256(self) -> str:
        return self.payload.prompt_sha256

    @property
    def tool_policy_sha256(self) -> str:
        return self.payload.tool_policy_sha256

    @property
    def memory_retrieval_policy_sha256(self) -> str:
        return self.payload.memory_retrieval_policy_sha256

    @property
    def playbook_ids(self) -> tuple[str, ...]:
        return self.payload.playbook_ids

    @property
    def parent_version_id(self) -> str | None:
        return self.payload.parent_version_id

    @property
    def deployment_state(self) -> AgentDeploymentState:
        return self.payload.deployment_state

    @property
    def task_id(self) -> str:
        return self.payload.task_id

    @property
    def created_at(self) -> dt.datetime:
        return self.payload.created_at

    @property
    def created_session_date(self) -> dt.date:
        return self.payload.created_session_date

    @property
    def order_authority(self) -> Literal[False]:
        return self.payload.order_authority

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.version_id != expected:
            raise DayAgentVersionStoreError("agent_version_identity_invalid")
        return self


class AgentChangeProposal(DayAgentVersionModel):
    proposal_id: str = Field(pattern=_SHA256)
    version_id: str = Field(pattern=_SHA256)
    parent_version_id: str = Field(pattern=_SHA256)
    problem_stage: DayDecisionStage
    allowed_changes: tuple[AgentChangeKind, ...] = Field(min_length=1, max_length=1)
    change_content: str = Field(min_length=16, max_length=8_000)
    change_content_sha256: str = Field(pattern=_SHA256)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    created_at: AwareDatetime
    order_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        if (
            hashlib.sha256(self.change_content.encode()).hexdigest() != self.change_content_sha256
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
        ):
            raise DayAgentVersionStoreError("agent_change_proposal_invalid")
        return self


class AgentPromotionRecommendation(DayAgentVersionModel):
    recommendation_id: str = Field(pattern=_SHA256)
    champion_version_id: str = Field(pattern=_SHA256)
    challenger_version_id: str = Field(pattern=_SHA256)
    decision: AgentPromotionDecision
    evaluated_session_dates: tuple[dt.date, ...] = Field(min_length=1)
    paired_snapshot_ids: tuple[str, ...] = Field(min_length=1)
    champion_score: float
    challenger_score: float
    reason_codes: tuple[str, ...] = Field(min_length=1)
    evaluated_at: AwareDatetime
    deployment_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_recommendation(self) -> Self:
        if (
            not all(math.isfinite(item) for item in (self.champion_score, self.challenger_score))
            or self.evaluated_session_dates != tuple(sorted(set(self.evaluated_session_dates)))
            or len(self.paired_snapshot_ids) < len(self.evaluated_session_dates)
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
        ):
            raise DayAgentVersionStoreError("agent_promotion_recommendation_invalid")
        return self


def build_agent_version(
    *,
    model_role_bindings: tuple[AgentModelRoleBinding, ...],
    prompt_sha256: str,
    tool_policy_sha256: str,
    memory_retrieval_policy_sha256: str,
    playbook_ids: tuple[str, ...],
    parent_version_id: str | None,
    creation_evidence_ids: tuple[str, ...],
    deployment_state: AgentDeploymentState,
    task_id: str,
    created_at: dt.datetime,
    created_session_date: dt.date,
) -> AgentVersion:
    payload = AgentVersionPayload(
        model_role_bindings=model_role_bindings,
        prompt_sha256=prompt_sha256,
        tool_policy_sha256=tool_policy_sha256,
        memory_retrieval_policy_sha256=memory_retrieval_policy_sha256,
        playbook_ids=playbook_ids,
        parent_version_id=parent_version_id,
        creation_evidence_ids=creation_evidence_ids,
        deployment_state=deployment_state,
        task_id=task_id,
        created_at=created_at,
        created_session_date=created_session_date,
    )
    version_id = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    return AgentVersion(version_id=version_id, payload=payload)


__all__ = (
    "AgentChangeKind",
    "AgentChangeProposal",
    "AgentDeploymentState",
    "AgentModelRoleBinding",
    "AgentPromotionDecision",
    "AgentPromotionRecommendation",
    "AgentVersion",
    "AgentVersionPayload",
    "DayAgentVersionStoreError",
    "build_agent_version",
)
