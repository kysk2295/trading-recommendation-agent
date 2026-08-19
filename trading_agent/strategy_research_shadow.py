from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Literal, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_ledger import AgentResearchStateEvent
from trading_agent.strategy_research_models import EvidenceRef
from trading_agent.strategy_research_types import (
    CanonicalModel,
    EvidenceKind,
    ResearchAgentId,
    TerminalOutcome,
    aware,
)


@dataclass(frozen=True, slots=True)
class StrategyResearchShadowError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class FutureShadowPolicy(CanonicalModel):
    future_sample_target: int = Field(default=40, ge=1)
    maximum_ci_width: float = Field(default=0.02, gt=0, allow_inf_nan=False)


class FutureShadowObservation(CanonicalModel):
    observation_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    owner_agent_id: ResearchAgentId
    observed_at: dt.datetime
    source_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    sample_count: int = Field(ge=1)
    ci_width: float = Field(ge=0, allow_inf_nan=False)
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        evidence_ids = tuple(item.evidence_id for item in self.source_refs)
        if not aware(self.observed_at) or evidence_ids != tuple(sorted(set(evidence_ids))):
            raise StrategyResearchShadowError("shadow_observation_invalid")
        if any(item.available_at > self.observed_at for item in self.source_refs):
            raise StrategyResearchShadowError("shadow_observation_invalid")
        return self


def append_future_shadow_observation(
    store: ExperimentLedgerStore,
    observation: FutureShadowObservation,
    policy: FutureShadowPolicy,
) -> tuple[AgentResearchStateEvent, bool]:
    checked = FutureShadowObservation.model_validate(observation.model_dump(mode="python"))
    checked_policy = FutureShadowPolicy.model_validate(policy.model_dump(mode="python"))
    event_id = hashlib.sha256(f"shadow-observation:{checked.observation_id}".encode()).hexdigest()
    payload_sha256 = hashlib.sha256(
        f"{checked.content_sha256}:{checked_policy.content_sha256}".encode()
    ).hexdigest()
    reader = ExperimentLedgerReader(store.path)
    existing = reader.strategy_research_agent_state_event(event_id)
    if existing is not None:
        if existing.shadow_observation_sha256 != payload_sha256:
            raise StrategyResearchShadowError("shadow_observation_conflict")
        return existing, False
    reveals = tuple(
        reveal
        for reveal in reader.strategy_research_sanitized_reveals(checked.owner_agent_id)
        if reveal.sanitized_result.result_id == checked.result_id
    )
    if len(reveals) != 1:
        raise StrategyResearchShadowError("shadow_result_mismatch")
    reveal = reveals[0]
    result = reveal.sanitized_result
    if result.hypothesis_id != checked.hypothesis_id or result.owner_agent_id is not checked.owner_agent_id:
        raise StrategyResearchShadowError("shadow_result_mismatch")
    match result.outcome:
        case TerminalOutcome.SUPPORTED:
            pass
        case TerminalOutcome.REFUTED | TerminalOutcome.INCONCLUSIVE:
            raise StrategyResearchShadowError("shadow_requires_supported_result")
        case unreachable:
            assert_never(unreachable)
    boundary = max(result.evaluated_at, reveal.revealed_at)
    source_times = tuple(time for item in checked.source_refs for time in (item.as_of, item.available_at))
    if checked.observed_at <= boundary or any(time <= boundary for time in source_times):
        raise StrategyResearchShadowError("shadow_time_not_future")
    real_sources = True
    for item in checked.source_refs:
        match item.source_kind:
            case EvidenceKind.REAL:
                pass
            case EvidenceKind.REPLAY:
                raise StrategyResearchShadowError("shadow_replay_forbidden")
            case EvidenceKind.FIXTURE | EvidenceKind.SYNTHETIC | EvidenceKind.BACKTEST:
                real_sources = False
            case unreachable:
                assert_never(unreachable)
    manifests = tuple(
        item
        for item in reader.strategy_research_preregistrations()
        if item.hypothesis.hypothesis_id == checked.hypothesis_id
    )
    if len(manifests) != 1 or manifests[0].hypothesis.agent_id is not checked.owner_agent_id:
        raise StrategyResearchShadowError("shadow_hypothesis_mismatch")
    hypothesis = manifests[0].hypothesis
    ci_token = f"{checked_policy.maximum_ci_width:g}"
    if (
        checked_policy.future_sample_target != hypothesis.minimum_observations
        or ci_token not in hypothesis.power_or_ci_gate
    ):
        raise StrategyResearchShadowError("shadow_policy_mismatch")
    history = reader.strategy_research_agent_state(checked.owner_agent_id)
    previous = history[-1] if history else None
    prior_shadow = next((event for event in reversed(history) if event.shadow_observation_id is not None), None)
    prior_samples = 0 if prior_shadow is None else prior_shadow.shadow_sample_count
    sample_count = prior_samples + checked.sample_count
    sufficient = (
        real_sources
        and sample_count >= checked_policy.future_sample_target
        and checked.ci_width <= checked_policy.maximum_ci_width
    )
    event = AgentResearchStateEvent(
        event_id=event_id,
        agent_id=checked.owner_agent_id,
        sequence=1 if previous is None else previous.sequence + 1,
        last_event_id=f"shadow:{checked.observation_id}",
        last_available_at=max(item.available_at for item in checked.source_refs),
        version=1,
        hypothesis_id=checked.hypothesis_id,
        attempt_id=None if previous is None else previous.attempt_id,
        state="paper_candidate" if sufficient else "forward_shadow",
        lease_until=None,
        checkpoint_sha256=None,
        retry_count=0,
        next_retry_at=None,
        next_due_at=None,
        next_maturity_at=None,
        reason="owner_approval_required" if sufficient else "future_shadow_information_pending",
        evidence_refs=tuple(sorted(item.evidence_id for item in checked.source_refs)),
        shadow_observation_id=checked.observation_id,
        shadow_observation_sha256=payload_sha256,
        shadow_result_id=checked.result_id,
        shadow_sample_count=sample_count,
        shadow_sample_target=checked_policy.future_sample_target,
        shadow_information_sufficient=sufficient,
        owner_approval_required=sufficient,
    )
    with store.writer() as writer:
        created = writer.append_strategy_research_agent_state(event)
    return event, created


__all__ = (
    "FutureShadowObservation",
    "FutureShadowPolicy",
    "StrategyResearchShadowError",
    "append_future_shadow_observation",
)
