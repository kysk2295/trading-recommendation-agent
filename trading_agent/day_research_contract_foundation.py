from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.day_hypothesis_models import HypothesisFamily
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

NOW = dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)
SHA_CODE = "a" * 64
SHA_DATA = "b" * 64
SHA_INPUT = "c" * 64
SHA_MODEL = "d" * 64
SHA_EVALUATOR = "e" * 64
MULTIPLE_TESTING_FAMILY = "synthetic-dual-market-contract-v1"


@dataclass(frozen=True, slots=True)
class SyntheticContractFoundation:
    family: HypothesisFamily
    manifest: PreregistrationManifest


def build_synthetic_contract_foundation() -> SyntheticContractFoundation:
    return SyntheticContractFoundation(family=_family(), manifest=_manifest())


def _family() -> HypothesisFamily:
    payload = {
        "family_id": "",
        "parent_family_id": None,
        "canonical_question": "Does a completed-bar synthetic signal preserve the same contract across KR and US?",
        "economic_mechanism": "A shared mechanism is represented by market-scoped versions without shared authority.",
        "alternative_explanations": ("market_microstructure", "sampling_noise"),
        "counterfactual_baseline": "market-scoped zero-signal baseline",
        "created_by": "day_research_contract_smoke",
        "created_at": NOW - dt.timedelta(minutes=9),
        "source_lineage": ("fixture:synthetic-contract-only",),
    }
    return HypothesisFamily.model_validate(payload | {"family_id": HypothesisFamily.canonical_id_for(payload)})


def _manifest() -> PreregistrationManifest:
    source = EvidenceRef(
        evidence_id="synthetic-contract-evidence-v1",
        source_id="synthetic-contract-source-v1",
        source_kind=EvidenceKind.SYNTHETIC,
        evidence_use=EvidenceUse.WIRING_ONLY,
        live_eligibility_policy=LiveEligibilityPolicy.WIRING_ONLY_NO_LIVE_USE,
        as_of=NOW - dt.timedelta(minutes=30),
        available_at=NOW - dt.timedelta(minutes=29),
        payload_sha256=SHA_INPUT,
    )
    observation = EvidenceObservation(
        observation_id="synthetic-contract-observation-v1",
        owner_agent_id=ResearchAgentId.CROSS_SECTIONAL_QUANT,
        observed_at=NOW - dt.timedelta(minutes=15),
        as_of=source.as_of,
        universe_definition="synthetic liquid-equity contract universe",
        universe_snapshot_id="synthetic-dual-market-universe-v1",
        universe_observed_at=NOW - dt.timedelta(minutes=25),
        predictor_formula="synthetic completed-bar score",
        predictor_observed_at=NOW - dt.timedelta(minutes=20),
        target_matures_at=NOW + dt.timedelta(minutes=10),
        coverage_fraction=1.0,
        source_refs=(source,),
    )
    hypothesis = ImmutableHypothesis(
        hypothesis_id="synthetic-day-contract-hypothesis-v1",
        parent_hypothesis_id=None,
        search_family_id="synthetic-day-contract-family-v1",
        agent_id=ResearchAgentId.CROSS_SECTIONAL_QUANT,
        owner_family="day_research",
        lane_id="dual_market_contract_smoke",
        created_at=NOW - dt.timedelta(minutes=10),
        created_by="day_research_contract_smoke",
        source_refs=(source,),
        evidence_hashes=(SHA_INPUT,),
        evidence_use=EvidenceUse.WIRING_ONLY,
        observation=observation,
        point_in_time_policy="synthetic wiring only; never eligible for live use",
        universe_definition=observation.universe_definition,
        universe_snapshot_id=observation.universe_snapshot_id,
        universe_observed_at=observation.universe_observed_at,
        instrument_scope="synthetic KR and US equity contracts",
        predictor_formula=observation.predictor_formula,
        sampling_timestamp=observation.predictor_observed_at,
        target_formula="synthetic next completed-bar return",
        target_horizon=TargetHorizon(duration=dt.timedelta(minutes=30)),
        target_matures_at=observation.target_matures_at,
        expected_direction=ExpectedDirection.POSITIVE,
        entry_rule="enter_next_completed_bar",
        exit_rule="exit_at_target_horizon",
        stop_rule="stop_first_on_same_bar_collision",
        invalidation_rule="invalidate_when_market_partition_is_missing",
        economic_mechanism="synthetic shared mechanism for contract validation only",
        alternative_explanations=("market_microstructure", "sampling_noise"),
        counterfactual_baseline="market-scoped zero-signal baseline",
        baseline_id="synthetic-zero-signal-v1",
        cost_model_id="synthetic-market-cost-v1",
        slippage_model_id="synthetic-bounded-slippage-v1",
        primary_metric="synthetic contract completion",
        secondary_metrics=("market_partition_integrity",),
        falsification_rule="refute when any cross-market identity is observed",
        free_parameters=(
            FreeParameter(
                name="synthetic_threshold",
                candidate_values=(1.0, 2.0),
                lower_bound=1.0,
                upper_bound=2.0,
            ),
        ),
        search_budget=SearchBudget(
            max_parameter_combinations=2,
            max_attempts=2,
            max_cpu_seconds=60,
        ),
        minimum_observations=20,
        power_or_ci_gate="synthetic wiring-only gate",
        multiple_testing_family=MULTIPLE_TESTING_FAMILY,
        max_attempts=2,
        train_period=ResearchPeriod(
            start=NOW - dt.timedelta(days=180),
            end=NOW - dt.timedelta(days=90),
        ),
        validation_period=ResearchPeriod(
            start=NOW - dt.timedelta(days=89),
            end=NOW - dt.timedelta(days=1),
        ),
        holdout_period_sealed_ref=SealedHoldoutRef(
            seal_id="synthetic-contract-holdout-v1",
            commitment_sha256=SHA_DATA,
            sealed_at=NOW - dt.timedelta(days=181),
            owner="contract-smoke",
            access_policy="synthetic wiring only",
        ),
        holdout_access_policy="synthetic wiring only",
        model_hash=SHA_MODEL,
        prompt_hash=SHA_INPUT,
        protocol_version="synthetic-day-contract-v1",
        code_sha256=SHA_CODE,
        data_manifest_sha256=SHA_DATA,
        status=HypothesisStatus.PREREGISTERED,
        trading_authority=False,
        profitability_claim=False,
    )
    return PreregistrationManifest.from_hypothesis(
        hypothesis,
        preregistered_at=hypothesis.created_at,
    )


__all__ = (
    "MULTIPLE_TESTING_FAMILY",
    "NOW",
    "SHA_CODE",
    "SHA_DATA",
    "SHA_EVALUATOR",
    "SHA_INPUT",
    "SHA_MODEL",
    "SyntheticContractFoundation",
    "build_synthetic_contract_foundation",
)
