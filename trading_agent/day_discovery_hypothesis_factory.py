from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.day_hypothesis_models import (
    CostModelDeclaration,
    HypothesisFamily,
    HypothesisVersion,
)
from trading_agent.day_hypothesis_models import (
    FreeParameter as DayFreeParameter,
)
from trading_agent.day_hypothesis_models import (
    SearchBudget as DaySearchBudget,
)
from trading_agent.day_hypothesis_models import (
    TargetHorizon as DayTargetHorizon,
)
from trading_agent.day_strategy_capsule import generated_protocol_bundle_sha256
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import ProposedHypothesis
from trading_agent.strategy_research_evidence_service import StrategyResearchEvidenceRejected
from trading_agent.strategy_research_models import (
    EvidenceObservation,
    EvidenceRef,
    FreeParameter,
    ImmutableHypothesis,
    PreregistrationManifest,
    ResearchPeriod,
    SealedHoldoutRef,
    SearchBudget,
    TargetHorizon,
)
from trading_agent.strategy_research_types import (
    EvidenceKind,
    EvidenceUse,
    ExpectedDirection,
    HypothesisStatus,
    LiveEligibilityPolicy,
    ResearchAgentId,
)


@dataclass(frozen=True, slots=True)
class DayHypothesisBuildInput:
    market_id: MarketId
    observed_at: dt.datetime
    completed_bar_at: dt.datetime
    first_eligible_completed_bar_at: dt.datetime
    universe_snapshot_id: str
    universe_snapshot_at: dt.datetime
    source_refs: tuple[str, ...]
    data_manifest_sha256: str
    search_budget: int


def day_open_methodology_tags(proposal: ProposedHypothesis) -> tuple[str, ...]:
    tags = proposal.strategy_draft.methodology_tags
    if not tags or tags != tuple(sorted(set(tags))) or any(
        not tag or tag != tag.strip() or len(tag) > 80 for tag in tags
    ):
        raise StrategyResearchEvidenceRejected("day_methodology_tags_invalid")
    return tags


def build_day_hypothesis_contracts(
    proposal: ProposedHypothesis,
    source: DayHypothesisBuildInput,
    *,
    terminal: bool,
) -> tuple[HypothesisFamily, HypothesisVersion, PreregistrationManifest]:
    family_payload = {
        "family_id": "",
        "parent_family_id": None,
        "canonical_question": proposal.card.hypothesis.hypothesis,
        "economic_mechanism": proposal.card.economic_mechanism,
        "alternative_explanations": ("confounding_market_regime",),
        "counterfactual_baseline": _terminal_text(
            proposal.card.counterfactual_baseline,
            "invalid_ai_counterfactual_declaration",
            terminal=terminal,
        ),
        "created_by": "day_discovery",
        "created_at": source.observed_at,
        "source_lineage": source.source_refs,
    }
    family = HypothesisFamily.model_validate(
        family_payload | {"family_id": HypothesisFamily.canonical_id_for(family_payload)}
    )
    code_sha256 = hashlib.sha256(proposal.strategy_draft.source_code.encode()).hexdigest()
    parameter_names = proposal.strategy_draft.free_parameters or ("fixed_configuration",)
    budget = min(source.search_budget, 10_000)
    version_payload = {
        "hypothesis_version_id": "",
        "family_id": family.family_id,
        "parent_version_id": None,
        "market_id": source.market_id,
        "universe_snapshot_id": source.universe_snapshot_id,
        "universe_snapshot_at": source.universe_snapshot_at,
        "source_refs": source.source_refs,
        "methodology_tags": _version_methodology_tags(proposal, terminal=terminal),
        "primary_evaluation_owner": "day_discovery",
        "evaluation_cadence": "each_completed_bar",
        "predictor": "host_bounded_completed_bar_evidence",
        "sampling_timestamp": source.completed_bar_at,
        "target": "future_only_shadow_signal",
        "target_horizon": DayTargetHorizon(
            duration=source.first_eligible_completed_bar_at - source.completed_bar_at
        ),
        "expected_direction": ExpectedDirection.POSITIVE,
        "entry_rule": "host_validates_generated_candidate",
        "exit_rule": "host_applies_preregistered_horizon",
        "stop_rule": "host_stop_first_on_same_bar_collision",
        "invalidation_rule": _terminal_text(
            proposal.card.hypothesis.falsification_rule,
            "invalid_ai_falsification_declaration",
            terminal=terminal,
        ),
        "threshold": Decimal(0),
        "cost_model": _cost_model(source.market_id),
        "free_parameters": tuple(
            DayFreeParameter(name=name, values=(Decimal(0), Decimal(1)))
            for name in sorted(set(parameter_names))
        ),
        "search_budget": DaySearchBudget(
            max_parameter_combinations=budget,
            max_attempts=budget,
            max_cpu_seconds=60,
        ),
        "multiple_testing_family": family.family_id,
        "model_sha256": hashlib.sha256(proposal.llm_receipt.model_id.encode()).hexdigest(),
        "prompt_sha256": proposal.llm_receipt.prompt_sha256,
        "code_sha256": code_sha256,
        "data_manifest_sha256": source.data_manifest_sha256,
        "protocol_sha256": generated_protocol_bundle_sha256(),
        "created_at": source.observed_at,
        "registration_completed_bar_at": source.completed_bar_at,
        "first_shadow_eligible_at": source.first_eligible_completed_bar_at,
        "trading_authority": False,
        "profitability_claim": False,
    }
    version = HypothesisVersion.model_validate(
        version_payload
        | {"hypothesis_version_id": HypothesisVersion.canonical_id_for(version_payload)}
    )
    preregistration = PreregistrationManifest.from_hypothesis(
        _immutable_hypothesis(
            proposal,
            version,
            source,
            counterfactual_baseline=family.counterfactual_baseline,
        ),
        preregistered_at=source.observed_at,
    )
    return family, version, preregistration


def _immutable_hypothesis(
    proposal: ProposedHypothesis,
    version: HypothesisVersion,
    source: DayHypothesisBuildInput,
    *,
    counterfactual_baseline: str,
) -> ImmutableHypothesis:
    evidence = EvidenceRef(
        evidence_id=hashlib.sha256("|".join(source.source_refs).encode()).hexdigest(),
        source_id=source.source_refs[0],
        source_kind=EvidenceKind.FIXTURE,
        evidence_use=EvidenceUse.WIRING_ONLY,
        live_eligibility_policy=LiveEligibilityPolicy.WIRING_ONLY_NO_LIVE_USE,
        as_of=source.universe_snapshot_at,
        available_at=source.universe_snapshot_at,
        payload_sha256=source.data_manifest_sha256,
    )
    observation = EvidenceObservation(
        observation_id=hashlib.sha256(
            f"{version.hypothesis_version_id}:observation".encode()
        ).hexdigest(),
        owner_agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        observed_at=source.observed_at,
        as_of=source.universe_snapshot_at,
        universe_definition=source.market_id.value,
        universe_snapshot_id=source.universe_snapshot_id,
        universe_observed_at=source.universe_snapshot_at,
        predictor_formula="host_bounded_completed_bar_evidence",
        predictor_observed_at=source.completed_bar_at,
        target_matures_at=source.first_eligible_completed_bar_at,
        coverage_fraction=1.0,
        source_refs=(evidence,),
    )
    start = source.observed_at - dt.timedelta(days=30)
    return ImmutableHypothesis(
        hypothesis_id=version.hypothesis_version_id,
        parent_hypothesis_id=None,
        search_family_id=version.family_id,
        agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        owner_family="day_trading",
        lane_id="day_discovery",
        created_at=source.observed_at,
        created_by="day_discovery",
        source_refs=(evidence,),
        evidence_hashes=(source.data_manifest_sha256,),
        evidence_use=EvidenceUse.WIRING_ONLY,
        observation=observation,
        point_in_time_policy="available_at_not_after_predictor",
        universe_definition=source.market_id.value,
        universe_snapshot_id=source.universe_snapshot_id,
        universe_observed_at=source.universe_snapshot_at,
        instrument_scope="bounded_evidence_view",
        predictor_formula=observation.predictor_formula,
        sampling_timestamp=source.completed_bar_at,
        target_formula="future_only_shadow_signal",
        target_horizon=TargetHorizon(
            duration=source.first_eligible_completed_bar_at - source.completed_bar_at
        ),
        target_matures_at=source.first_eligible_completed_bar_at,
        expected_direction=ExpectedDirection.POSITIVE,
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        invalidation_rule=version.invalidation_rule,
        economic_mechanism=proposal.card.economic_mechanism,
        alternative_explanations=("confounding_market_regime",),
        counterfactual_baseline=counterfactual_baseline,
        baseline_id="host_baseline_v1",
        cost_model_id=version.cost_model.model_id,
        slippage_model_id="bounded_intraday_slippage_v1",
        primary_metric="future_signal_count",
        secondary_metrics=("blocked_count",),
        falsification_rule=version.invalidation_rule,
        free_parameters=(
            FreeParameter(
                name="fixed_configuration",
                candidate_values=(0.0, 1.0),
                lower_bound=0.0,
                upper_bound=1.0,
            ),
        ),
        search_budget=SearchBudget(
            max_parameter_combinations=2,
            max_attempts=min(2, source.search_budget),
            max_cpu_seconds=60,
        ),
        minimum_observations=20,
        power_or_ci_gate="preregistered_exact_interval",
        multiple_testing_family=version.multiple_testing_family,
        max_attempts=min(2, source.search_budget),
        train_period=ResearchPeriod(
            start=start,
            end=source.observed_at - dt.timedelta(days=20),
        ),
        validation_period=ResearchPeriod(
            start=source.observed_at - dt.timedelta(days=19),
            end=source.observed_at - dt.timedelta(days=10),
        ),
        holdout_period_sealed_ref=SealedHoldoutRef(
            seal_id=hashlib.sha256(f"{version.hypothesis_version_id}:seal".encode()).hexdigest(),
            commitment_sha256=hashlib.sha256(
                f"sealed:{version.hypothesis_version_id}".encode()
            ).hexdigest(),
            sealed_at=start - dt.timedelta(days=1),
            owner="day_discovery_holdout",
            access_policy="future_reviewer_only",
        ),
        holdout_access_policy="future_reviewer_only",
        model_hash=version.model_sha256,
        prompt_hash=version.prompt_sha256,
        protocol_version="day_discovery_v1",
        code_sha256=version.code_sha256,
        data_manifest_sha256=source.data_manifest_sha256,
        status=HypothesisStatus.PREREGISTERED,
    )


def _version_methodology_tags(
    proposal: ProposedHypothesis, *, terminal: bool
) -> tuple[str, ...]:
    try:
        return day_open_methodology_tags(proposal)
    except StrategyResearchEvidenceRejected:
        if terminal:
            return ("invalid_ai_methodology_declaration",)
        raise


def _terminal_text(value: str, sentinel: str, *, terminal: bool) -> str:
    if value.strip():
        return value
    if terminal:
        return sentinel
    raise StrategyResearchEvidenceRejected("day_hypothesis_contract_invalid")


def _cost_model(market: MarketId) -> CostModelDeclaration:
    return CostModelDeclaration(
        model_id=f"{market.value}_cost_v1",
        commission_bps=Decimal(1),
        slippage_bps=Decimal(2),
    )


__all__ = (
    "DayHypothesisBuildInput",
    "build_day_hypothesis_contracts",
    "day_open_methodology_tags",
)
