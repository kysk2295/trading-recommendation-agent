from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Protocol, assert_never

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.day_agent_challenger_publisher import (
    DayAgentChallengerPublicationRequest,
    PublishedDayAgentChallenger,
)
from trading_agent.day_agent_change_patches import (
    CatalystInterpretationPatch,
    EntryPolicyPatch,
    ExecutionReviewPatch,
    ExitPolicyPatch,
    FlowInterpretationPatch,
    LeaderRankingPatch,
    MarketRegimePatch,
    ThemeSelectionPatch,
)
from trading_agent.day_agent_version_models import (
    AgentChangeKind,
    AgentChangeProposal,
    AgentDeploymentState,
    AgentVersion,
    AgentVersionPatch,
    DayAgentVersionStoreError,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import (
    DayDecisionStage,
    MarketCloseReport,
    MarketCloseReportPayload,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


class ProposedAgentChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patch: AgentVersionPatch = Field(discriminator="kind")


class DayAgentChangeAuthor(Protocol):
    def propose(self, stage: DayDecisionStage, champion: AgentVersion) -> ProposedAgentChange: ...


class DayAgentChallengerPublisher(Protocol):
    def publish(self, request: DayAgentChallengerPublicationRequest) -> PublishedDayAgentChallenger: ...


@dataclass(frozen=True, slots=True)
class DayAgentLoopServices:
    store: DayAgentVersionStore
    author: DayAgentChangeAuthor
    publisher: DayAgentChallengerPublisher


def run_loop_engineer(
    report: MarketCloseReport,
    champion: AgentVersion,
    services: DayAgentLoopServices,
) -> AgentChangeProposal:
    payload = report.payload
    if (
        champion.deployment_state is not AgentDeploymentState.CHAMPION
        or payload.agent_version_id != champion.version_id
        or not payload.diagnostics
        or payload.finalized_at < payload.watermark.finalized_through
    ):
        raise DayAgentVersionStoreError("loop_engineer_input_invalid")
    problem = min(payload.diagnostics, key=lambda item: (item.score, tuple(DayDecisionStage).index(item.stage)))
    allowed = _change_for_stage(problem.stage)
    authored = services.author.propose(problem.stage, champion)
    if authored.patch.kind is not allowed:
        raise DayAgentVersionStoreError("change_kind_not_allowed")
    patch_sha256 = hashlib.sha256(canonical_experiment_ledger_json(authored.patch).encode()).hexdigest()
    published = services.publisher.publish(
        DayAgentChallengerPublicationRequest(report=report, champion=champion, patch=authored.patch)
    )
    future_sessions = tuple(policy.payload.effective_session_date for policy in published.policies)
    if (
        not future_sessions
        or any(session_date <= payload.session_date for session_date in future_sessions)
        or any(policy.payload.final_report_id != report.report_id for policy in published.policies)
    ):
        raise DayAgentVersionStoreError("challenger_future_policy_invalid")
    challenger = _challenger(
        champion,
        authored.patch,
        published.capsule.capsule_id,
        problem.evidence_ids,
        payload,
        created_session_date=min(future_sessions),
    )
    _require_only_allowed_change(champion, challenger, authored.patch)
    proposal_payload = {
        "version_id": challenger.version_id,
        "parent_version_id": champion.version_id,
        "problem_stage": problem.stage,
        "allowed_changes": (allowed,),
        "patch": authored.patch,
        "patch_sha256": patch_sha256,
        "evidence_ids": problem.evidence_ids,
        "created_at": payload.finalized_at,
        "order_authority": False,
    }
    unsigned = AgentChangeProposal(proposal_id="0" * 64, **proposal_payload)
    proposal_id = hashlib.sha256(canonical_experiment_ledger_json(unsigned).encode()).hexdigest()
    proposal = unsigned.model_copy(update={"proposal_id": proposal_id})
    with services.store.writer() as writer:
        _ = writer.register_challenger(challenger)
        _ = writer.record_proposal(proposal)
    return proposal


def _challenger(
    champion: AgentVersion,
    patch: AgentVersionPatch,
    capsule_id: str,
    evidence_ids: tuple[str, ...],
    report: MarketCloseReportPayload,
    *,
    created_session_date: dt.date,
) -> AgentVersion:
    prompt_sha256 = champion.prompt_sha256
    tool_policy_sha256 = champion.tool_policy_sha256
    playbook_ids = (capsule_id,)
    rendered_sha256 = hashlib.sha256(canonical_experiment_ledger_json(patch).encode()).hexdigest()
    match patch:
        case MarketRegimePatch() | ThemeSelectionPatch() | CatalystInterpretationPatch() | FlowInterpretationPatch():
            prompt_sha256 = rendered_sha256
        case LeaderRankingPatch() | EntryPolicyPatch() | ExitPolicyPatch():
            pass
        case ExecutionReviewPatch():
            tool_policy_sha256 = rendered_sha256
        case unreachable:
            assert_never(unreachable)
    return build_agent_version(
        model_role_bindings=champion.model_role_bindings,
        prompt_sha256=prompt_sha256,
        tool_policy_sha256=tool_policy_sha256,
        memory_retrieval_policy_sha256=champion.memory_retrieval_policy_sha256,
        playbook_ids=playbook_ids,
        parent_version_id=champion.version_id,
        creation_evidence_ids=evidence_ids,
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=champion.task_id,
        created_at=report.finalized_at,
        created_session_date=created_session_date,
    )


def _change_for_stage(stage: DayDecisionStage) -> AgentChangeKind:
    match stage:
        case DayDecisionStage.MARKET_RECOGNITION:
            return AgentChangeKind.MARKET_REGIME_POLICY
        case DayDecisionStage.THEME_SELECTION:
            return AgentChangeKind.THEME_SELECTION_POLICY
        case DayDecisionStage.CATALYST_INTERPRETATION:
            return AgentChangeKind.CATALYST_INTERPRETATION_POLICY
        case DayDecisionStage.LEADER_SELECTION:
            return AgentChangeKind.LEADER_RANKING_POLICY
        case DayDecisionStage.FLOW_INTERPRETATION:
            return AgentChangeKind.FLOW_INTERPRETATION_POLICY
        case DayDecisionStage.ENTRY:
            return AgentChangeKind.ENTRY_POLICY
        case DayDecisionStage.EXIT:
            return AgentChangeKind.EXIT_POLICY
        case DayDecisionStage.EXECUTION_QUALITY:
            return AgentChangeKind.EXECUTION_REVIEW_POLICY
        case unreachable:
            assert_never(unreachable)


def _require_only_allowed_change(
    champion: AgentVersion,
    challenger: AgentVersion,
    patch: AgentVersionPatch,
) -> None:
    baseline = {
        "model_role_bindings": champion.model_role_bindings,
        "prompt_sha256": champion.prompt_sha256,
        "tool_policy_sha256": champion.tool_policy_sha256,
        "memory_retrieval_policy_sha256": champion.memory_retrieval_policy_sha256,
        "playbook_ids": champion.playbook_ids,
        "authority_boundary": champion.payload.authority_boundary,
    }
    candidate = {
        "model_role_bindings": challenger.model_role_bindings,
        "prompt_sha256": challenger.prompt_sha256,
        "tool_policy_sha256": challenger.tool_policy_sha256,
        "memory_retrieval_policy_sha256": challenger.memory_retrieval_policy_sha256,
        "playbook_ids": challenger.playbook_ids,
        "authority_boundary": challenger.payload.authority_boundary,
    }
    changed = {field for field in baseline if baseline[field] != candidate[field]}
    match patch:
        case MarketRegimePatch() | ThemeSelectionPatch() | CatalystInterpretationPatch() | FlowInterpretationPatch():
            expected = {"prompt_sha256", "playbook_ids"}
        case LeaderRankingPatch() | EntryPolicyPatch() | ExitPolicyPatch():
            expected = {"playbook_ids"}
        case ExecutionReviewPatch():
            expected = {"tool_policy_sha256", "playbook_ids"}
        case unreachable:
            assert_never(unreachable)
    if changed != expected:
        raise DayAgentVersionStoreError("change_structural_authority_invalid")


__all__ = (
    "DayAgentChallengerPublisher",
    "DayAgentChangeAuthor",
    "DayAgentLoopServices",
    "ProposedAgentChange",
    "run_loop_engineer",
)
