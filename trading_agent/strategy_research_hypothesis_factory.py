from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from trading_agent.research_agent_cycle_models import EvidenceId
from trading_agent.strategy_research_catalog import STRATEGY_RESEARCH_CATALOG
from trading_agent.strategy_research_evidence_service import (
    MarketContextEvidenceService,
    OpportunityEvidenceService,
    SourceBoundCandidate,
    SourceBoundMarketContext,
    SourceHypothesisRequest,
    StrategyResearchEvidenceRejected,
)
from trading_agent.strategy_research_methodologies import (
    StrategyResearchMethodology,
    strategy_research_methodology,
)
from trading_agent.strategy_research_models import (
    EvidenceObservation,
    FreeParameter,
    ImmutableHypothesis,
    ResearchPeriod,
    SealedHoldoutRef,
    SearchBudget,
    TargetHorizon,
)
from trading_agent.strategy_research_observation_builders import (
    MethodologyObservation,
    MethodologyObservationInput,
    SourceAuthorityReceipt,
    build_methodology_observation,
)
from trading_agent.strategy_research_policy import MethodologyPolicyError
from trading_agent.strategy_research_types import (
    EvidenceUse,
    HypothesisStatus,
    ResearchAgentId,
)


@dataclass(frozen=True, slots=True)
class SourceHypothesisArtifact:
    candidate: SourceBoundCandidate
    observation: EvidenceObservation
    hypothesis: ImmutableHypothesis
    artifact_refs: tuple[str, ...]
    methodology_observation: MethodologyObservation
    source_receipts: tuple[SourceAuthorityReceipt, ...]


@dataclass(frozen=True, slots=True)
class StrategyResearchHypothesisFactory:
    opportunities: OpportunityEvidenceService
    market_context: MarketContextEvidenceService

    def create_routed(
        self,
        evidence_id: EvidenceId,
        observed_at: dt.datetime,
    ) -> SourceHypothesisArtifact:
        candidate = self.opportunities.candidate(evidence_id, observed_at)
        owner = _route_owner(candidate.opportunity.strategy_lane.strategy_id)
        return self.create(SourceHypothesisRequest(evidence_id, owner, observed_at))

    def create(self, request: SourceHypothesisRequest) -> SourceHypothesisArtifact:
        candidate = self.opportunities.candidate(request.opportunity_evidence_id, request.observed_at)
        owner = _route_owner(candidate.opportunity.strategy_lane.strategy_id)
        if request.owner_agent_id is not owner:
            raise StrategyResearchEvidenceRejected("owner_mismatch")
        context = self.market_context.current(
            candidate.opportunity.strategy_lane.market_id.value,
            request.observed_at,
        )
        policy = strategy_research_methodology(owner)
        source_refs = tuple(sorted((candidate.source_ref, context.source_ref), key=lambda item: item.source_id))
        receipts = request.source_receipts or _legacy_momentum_receipts(
            owner,
            candidate,
            context,
        )
        try:
            methodology_observation = build_methodology_observation(
                MethodologyObservationInput(owner, request.observed_at, receipts)
            )
        except MethodologyPolicyError as error:
            raise StrategyResearchEvidenceRejected(error.reason) from None
        if not methodology_observation.ready:
            raise StrategyResearchEvidenceRejected(
                methodology_observation.waiting_reason or "methodology_source_waiting"
            )
        if not set(methodology_observation.source_ids).issubset(item.source_id for item in source_refs):
            raise StrategyResearchEvidenceRejected("methodology_evidence_ref_mismatch")
        source_material = ":".join(
            (owner.value, *(item.source_id for item in source_refs), *(item.payload_sha256 for item in source_refs))
        )
        observation_id = f"observation-{owner.value}-{_sha(source_material)[:24]}"
        predictor_observed_at = methodology_observation.predictor_available_at
        universe_observed_at = max(item.as_of for item in source_refs)
        target_matures_at = predictor_observed_at + policy.target_horizon
        observation = EvidenceObservation(
            observation_id=observation_id,
            owner_agent_id=owner,
            observed_at=predictor_observed_at,
            as_of=min(item.as_of for item in source_refs),
            universe_definition=(
                "point-in-time eligible KR equities in the immutable opportunity snapshot"
                if candidate.opportunity.strategy_lane.market_id.value == "kr_equities"
                else "point-in-time eligible US equities in the immutable opportunity snapshot"
            ),
            universe_snapshot_id=candidate.opportunity.opportunity_id,
            universe_observed_at=universe_observed_at,
            predictor_formula=policy.predictor_grammar,
            predictor_observed_at=predictor_observed_at,
            target_matures_at=target_matures_at,
            coverage_fraction=1.0,
            source_refs=source_refs,
        )
        hypothesis = _hypothesis(candidate, observation, policy)
        artifact_refs = tuple(
            sorted(
                (
                    *(item.source_id for item in source_refs),
                    observation.observation_id,
                    hypothesis.hypothesis_id,
                )
            )
        )
        return SourceHypothesisArtifact(
            candidate,
            observation,
            hypothesis,
            artifact_refs,
            methodology_observation,
            receipts,
        )


def _legacy_momentum_receipts(
    owner: ResearchAgentId,
    candidate: SourceBoundCandidate,
    context: SourceBoundMarketContext,
) -> tuple[SourceAuthorityReceipt, ...]:
    if owner is not ResearchAgentId.INTRADAY_MOMENTUM:
        raise StrategyResearchEvidenceRejected(f"{owner.value}_source_receipt_missing")
    return (
        SourceAuthorityReceipt(
            "consolidated_completed_bar",
            candidate.source_ref.source_id,
            candidate.source_ref.as_of,
            candidate.source_ref.available_at,
            True,
            True,
        ),
        SourceAuthorityReceipt(
            "fresh_actionable_spread",
            candidate.source_ref.source_id,
            candidate.source_ref.as_of,
            candidate.source_ref.available_at,
            True,
            True,
        ),
        SourceAuthorityReceipt(
            "current_market_session",
            context.source_ref.source_id,
            context.source_ref.as_of,
            context.source_ref.available_at,
            True,
            True,
            True,
        ),
    )


def _hypothesis(
    candidate: SourceBoundCandidate,
    observation: EvidenceObservation,
    policy: StrategyResearchMethodology,
) -> ImmutableHypothesis:
    owner = observation.owner_agent_id
    created_at = observation.observed_at
    identity = next(item for item in STRATEGY_RESEARCH_CATALOG if item.agent_id is owner)
    hypothesis_id = f"hypothesis-{owner.value}-{_sha(observation.content_sha256)[:24]}"
    train_start = created_at - dt.timedelta(days=180)
    train_end = created_at - dt.timedelta(days=91)
    validation_start = created_at - dt.timedelta(days=90)
    validation_end = created_at - dt.timedelta(days=1)
    policy_hash = _sha(f"{identity.content_sha256}:deterministic-source-builder-v1")
    data_hash = _sha(":".join(item.payload_sha256 for item in observation.source_refs))
    return ImmutableHypothesis(
        hypothesis_id=hypothesis_id,
        parent_hypothesis_id=None,
        search_family_id=f"source-bound-{owner.value}-v1",
        agent_id=owner,
        owner_family="strategy_research",
        lane_id=candidate.opportunity.strategy_lane.strategy_id,
        created_at=created_at,
        created_by=f"{owner.value}-deterministic-builder-v1",
        source_refs=observation.source_refs,
        evidence_hashes=tuple(item.payload_sha256 for item in observation.source_refs),
        evidence_use=EvidenceUse.RESEARCH,
        observation=observation,
        point_in_time_policy="use only immutable values available by predictor_observed_at",
        universe_definition=observation.universe_definition,
        universe_snapshot_id=observation.universe_snapshot_id,
        universe_observed_at=observation.universe_observed_at,
        instrument_scope=candidate.symbol,
        predictor_formula=observation.predictor_formula,
        sampling_timestamp=observation.predictor_observed_at,
        target_formula=policy.target_formula,
        target_horizon=TargetHorizon(duration=policy.target_horizon),
        target_matures_at=observation.target_matures_at,
        expected_direction=policy.expected_direction,
        entry_rule=policy.entry_rule,
        exit_rule=policy.exit_rule,
        stop_rule=policy.stop_rule,
        invalidation_rule="invalidate on missing, stale, revised, or non-current-session source evidence",
        economic_mechanism=identity.methodology,
        alternative_explanations=("market beta", "selection and liquidity effects"),
        counterfactual_baseline=policy.baseline_id,
        baseline_id=policy.baseline_id,
        cost_model_id=policy.cost_model_id,
        slippage_model_id="spread-impact-model-v2",
        primary_metric="studentized bootstrap mean net excess return",
        secondary_metrics=("hit rate", "maximum adverse excursion"),
        falsification_rule="refute when the preregistered studentized interval is entirely non-positive",
        free_parameters=(
            FreeParameter(name="rank_cutoff", candidate_values=(0.1, 0.2), lower_bound=0.1, upper_bound=0.2),
        ),
        search_budget=SearchBudget(max_parameter_combinations=2, max_attempts=2, max_cpu_seconds=60),
        minimum_observations=40,
        power_or_ci_gate=(f"{policy.resampling_method.value} CI width <= 0.02 with at least 40 observations"),
        multiple_testing_family=f"source-bound-{owner.value}-v1",
        max_attempts=2,
        train_period=ResearchPeriod(start=train_start, end=train_end),
        validation_period=ResearchPeriod(start=validation_start, end=validation_end),
        holdout_period_sealed_ref=SealedHoldoutRef(
            seal_id=f"sealed-{hypothesis_id}",
            commitment_sha256=_sha(f"{hypothesis_id}:sealed-holdout"),
            sealed_at=train_start - dt.timedelta(days=1),
            owner="science-kernel",
            access_policy="single reveal to independent reviewer only",
        ),
        holdout_access_policy="owner receives sanitized terminal reason codes only",
        model_hash=_sha("deterministic-no-llm-v1"),
        prompt_hash=policy_hash,
        protocol_version="strategy-research-source-v1",
        code_sha256=_sha("strategy-research-hypothesis-factory-v1"),
        data_manifest_sha256=data_hash,
        status=HypothesisStatus.DRAFTED,
    )


def _route_owner(strategy_id: str) -> ResearchAgentId:
    normalized = strategy_id.casefold()
    routes = (
        (("mean_reversion", "reversion", "dislocation"), ResearchAgentId.INTRADAY_MEAN_REVERSION),
        (("catalyst", "event", "news"), ResearchAgentId.CATALYST_EVENT),
        (("swing", "regime"), ResearchAgentId.SWING_TREND_REGIME),
        (("cross_sectional", "sector_neutral"), ResearchAgentId.CROSS_SECTIONAL_QUANT),
        (("derivative", "option", "volatility"), ResearchAgentId.DERIVATIVES_VOLATILITY),
        (("momentum", "breakout", "continuation"), ResearchAgentId.INTRADAY_MOMENTUM),
    )
    matches = tuple(owner for tokens, owner in routes if any(token in normalized for token in tokens))
    if len(matches) != 1:
        raise StrategyResearchEvidenceRejected("owner_route_unresolved")
    return matches[0]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = ("SourceHypothesisArtifact", "StrategyResearchHypothesisFactory")
