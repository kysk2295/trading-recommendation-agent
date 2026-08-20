from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol, assert_never

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.day_agent_version_models import (
    AgentChangeKind,
    AgentChangeProposal,
    AgentDeploymentState,
    AgentVersion,
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

_PROHIBITED = re.compile(
    r"\b(endpoints?|credentials?|broker|account\s+risk|quantity|safety|promotion|"
    r"audit\s+(?:delete|deletion|history))\b",
    re.IGNORECASE,
)


class ProposedAgentChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AgentChangeKind
    content: str = Field(min_length=16, max_length=8_000)


class DayAgentChangeAuthor(Protocol):
    def propose(self, stage: DayDecisionStage, champion: AgentVersion) -> ProposedAgentChange: ...


@dataclass(frozen=True, slots=True)
class DayAgentLoopServices:
    store: DayAgentVersionStore
    author: DayAgentChangeAuthor


def run_loop_engineer(
    report: MarketCloseReport | MarketCloseReportPayload,
    champion: AgentVersion,
    services: DayAgentLoopServices,
) -> AgentChangeProposal:
    match report:
        case MarketCloseReport(payload=payload):
            pass
        case MarketCloseReportPayload() as payload:
            pass
        case unreachable:
            assert_never(unreachable)
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
    if authored.kind is not allowed:
        raise DayAgentVersionStoreError("change_kind_not_allowed")
    if _PROHIBITED.search(authored.content) is not None:
        raise DayAgentVersionStoreError("change_prohibited")
    content_sha256 = hashlib.sha256(authored.content.encode()).hexdigest()
    challenger = _challenger(champion, authored.kind, content_sha256, problem.evidence_ids, payload)
    proposal_payload = {
        "version_id": challenger.version_id,
        "parent_version_id": champion.version_id,
        "problem_stage": problem.stage,
        "allowed_changes": (allowed,),
        "change_content": authored.content,
        "change_content_sha256": content_sha256,
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
    kind: AgentChangeKind,
    content_sha256: str,
    evidence_ids: tuple[str, ...],
    report: MarketCloseReportPayload,
) -> AgentVersion:
    prompt_sha256 = champion.prompt_sha256
    tool_policy_sha256 = champion.tool_policy_sha256
    match kind:
        case (
            AgentChangeKind.MARKET_REGIME_POLICY
            | AgentChangeKind.THEME_SELECTION_POLICY
            | AgentChangeKind.CATALYST_INTERPRETATION_POLICY
            | AgentChangeKind.LEADER_RANKING_POLICY
            | AgentChangeKind.FLOW_INTERPRETATION_POLICY
            | AgentChangeKind.ENTRY_POLICY
            | AgentChangeKind.EXIT_POLICY
        ):
            prompt_sha256 = content_sha256
        case AgentChangeKind.EXECUTION_REVIEW_POLICY:
            tool_policy_sha256 = content_sha256
        case unreachable:
            assert_never(unreachable)
    return build_agent_version(
        model_role_bindings=champion.model_role_bindings,
        prompt_sha256=prompt_sha256,
        tool_policy_sha256=tool_policy_sha256,
        memory_retrieval_policy_sha256=champion.memory_retrieval_policy_sha256,
        playbook_ids=champion.playbook_ids,
        parent_version_id=champion.version_id,
        creation_evidence_ids=evidence_ids,
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=champion.task_id,
        created_at=report.finalized_at,
        created_session_date=report.session_date,
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


__all__ = (
    "DayAgentChangeAuthor",
    "DayAgentLoopServices",
    "ProposedAgentChange",
    "run_loop_engineer",
)
