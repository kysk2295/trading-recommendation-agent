from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, replace

from pydantic import AwareDatetime, BaseModel, ConfigDict

from trading_agent.day_agent_version_models import AgentVersion, AgentVersionPatch
from trading_agent.day_hypothesis_models import HypothesisVersion
from trading_agent.day_learning_policy import ExplorationPolicy, ExplorationPolicyAction, ExplorationPolicyPayload
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import HypothesisRegistration, ResearchHypothesisCard
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.researcher_agent import ProposedHypothesis
from trading_agent.strategy_research_models import PreregistrationManifest


@dataclass(frozen=True, slots=True)
class DerivedSourceRequest:
    champion: AgentVersion
    parent_capsule: StrategyCapsule
    patch: AgentVersionPatch
    binding_sha256: str
    parent_source: str


@dataclass(frozen=True, slots=True)
class DerivedProposalRequest:
    template: ProposedHypothesis
    hypothesis_id: str
    source: str
    binding_sha256: str
    source_sha256: str
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class DerivedManifestRequest:
    parent: PreregistrationManifest
    hypothesis_id: str
    source_sha256: str
    binding_sha256: str
    preregistered_at: dt.datetime


@dataclass(frozen=True, slots=True)
class DerivedVersionRequest:
    parent: HypothesisVersion
    source_sha256: str
    binding_sha256: str
    created_at: dt.datetime


class DayAgentFutureShadowSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    session_date: dt.date
    calendar_snapshot_id: str
    effective_at: AwareDatetime


@dataclass(frozen=True, slots=True)
class FuturePolicyRequest:
    report: MarketCloseReport
    champion: AgentVersion
    capsule: StrategyCapsule
    session: DayAgentFutureShadowSession


def render_derived_source(request: DerivedSourceRequest) -> str:
    patch_json = canonical_experiment_ledger_json(request.patch)
    return (
        f'AGENT_VERSION_PATCH_SHA256 = "{request.binding_sha256}"\n'
        f'PARENT_AGENT_VERSION_ID = "{request.champion.version_id}"\n'
        f'PARENT_PLAYBOOK_CAPSULE_ID = "{request.parent_capsule.capsule_id}"\n'
        f'PATCH_JSON = {patch_json!r}\n'
        f"{request.parent_source}\n"
        "_PARENT_CREATE_STRATEGY = create_strategy\n"
        "def create_strategy(context):\n"
        "    parent_strategy = _PARENT_CREATE_STRATEGY(context)\n"
        "    class DerivedStrategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            inherited = parent_strategy.observe(bar, candidate)\n"
        "            if inherited is not None:\n"
        "                return inherited\n"
        "            if candidate is None:\n"
        "                return None\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['close'] - 1.0, "
        "'rationale': 'typed day-agent challenger'}\n"
        "    return DerivedStrategy()\n"
    )


def build_derived_proposal(request: DerivedProposalRequest) -> ProposedHypothesis:
    scope = ExperimentScope.model_validate(
        request.template.card.hypothesis.experiment_scope.model_dump(mode="python")
        | {"hypothesis_id": request.hypothesis_id}
    )
    registration = HypothesisRegistration.model_validate(
        request.template.card.hypothesis.model_dump(mode="python")
        | {
            "hypothesis_id": request.hypothesis_id,
            "experiment_scope": scope,
            "experiment_scope_key": experiment_scope_key(scope),
        }
    )
    card = ResearchHypothesisCard.model_validate(
        request.template.card.model_dump(mode="python") | {"hypothesis": registration}
    )
    return replace(
        request.template,
        card=card,
        llm_receipt=replace(
            request.template.llm_receipt,
            model_id="day-loop-derived-v1",
            prompt_sha256=request.binding_sha256,
            response_sha256=request.source_sha256,
            called_at=request.created_at,
        ),
        strategy_draft=replace(request.template.strategy_draft, source_code=request.source, free_parameters=()),
    )


def build_derived_manifest(request: DerivedManifestRequest) -> PreregistrationManifest:
    hypothesis = request.parent.hypothesis.model_copy(
        update={
            "hypothesis_id": request.hypothesis_id,
            "parent_hypothesis_id": request.parent.hypothesis.hypothesis_id,
            "code_sha256": request.source_sha256,
            "prompt_hash": request.binding_sha256,
            "holdout_period_sealed_ref": request.parent.hypothesis.holdout_period_sealed_ref.model_copy(
                update={"seal_id": f"day-loop-seal-{request.binding_sha256}"}
            ),
        }
    )
    return PreregistrationManifest.from_hypothesis(
        hypothesis,
        preregistered_at=request.preregistered_at,
    )


def build_derived_version(request: DerivedVersionRequest) -> HypothesisVersion:
    payload = request.parent.model_dump(mode="python") | {
        "hypothesis_version_id": "",
        "parent_version_id": request.parent.hypothesis_version_id,
        "prompt_sha256": request.binding_sha256,
        "code_sha256": request.source_sha256,
        "protocol_sha256": request.parent.protocol_sha256,
        "sampling_timestamp": request.created_at,
        "created_at": request.created_at,
        "registration_completed_bar_at": request.created_at + dt.timedelta(minutes=1),
        "first_shadow_eligible_at": request.created_at + dt.timedelta(minutes=2),
    }
    return HypothesisVersion.model_validate(
        payload | {"hypothesis_version_id": HypothesisVersion.canonical_id_for(payload)}
    )


def build_future_policy(request: FuturePolicyRequest) -> ExplorationPolicy:
    payload = ExplorationPolicyPayload(
        final_report_id=request.report.report_id,
        market_id=request.report.payload.market_id,
        action=ExplorationPolicyAction.KEEP,
        calendar_snapshot_id=request.session.calendar_snapshot_id,
        effective_session_date=request.session.session_date,
        effective_at=request.session.effective_at,
        active_capsule_ids=tuple(sorted((*request.champion.playbook_ids, request.capsule.capsule_id))),
        queued_capsule_ids=request.report.payload.next_session.queued_capsule_ids,
        feedback_decision_ids=(),
        policy_version="day-exploration-policy-v1",
    )
    return ExplorationPolicy(
        policy_id=hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest(),
        payload=payload,
    )


__all__ = (
    "DayAgentFutureShadowSession",
    "DerivedManifestRequest",
    "DerivedProposalRequest",
    "DerivedSourceRequest",
    "DerivedVersionRequest",
    "FuturePolicyRequest",
    "build_derived_manifest",
    "build_derived_proposal",
    "build_derived_version",
    "build_future_policy",
    "render_derived_source",
)
