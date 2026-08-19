from __future__ import annotations

import datetime as dt

from trading_agent.strategy_research_models import (
    EvidenceObservation,
    EvidenceRef,
    FreeParameter,
    ImmutableHypothesis,
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

NOW = dt.datetime(2026, 8, 19, 14, 0, tzinfo=dt.UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def source(*, kind: EvidenceKind = EvidenceKind.REAL) -> EvidenceRef:
    use = EvidenceUse.RESEARCH if kind is EvidenceKind.REAL else EvidenceUse.WIRING_ONLY
    live_policy = (
        LiveEligibilityPolicy.TASK3_CURRENT_SESSION_GATE_REQUIRED
        if kind is EvidenceKind.REAL
        else LiveEligibilityPolicy.WIRING_ONLY_NO_LIVE_USE
    )
    return EvidenceRef(
        evidence_id="evidence-sec-20260819-001",
        source_id="sec-receipt-20260819-001",
        source_kind=kind,
        evidence_use=use,
        live_eligibility_policy=live_policy,
        as_of=NOW - dt.timedelta(minutes=20),
        available_at=NOW - dt.timedelta(minutes=15),
        payload_sha256=SHA_A,
    )


def observation(*, kind: EvidenceKind = EvidenceKind.REAL) -> EvidenceObservation:
    evidence = source(kind=kind)
    return EvidenceObservation(
        observation_id="observation-catalyst-001",
        owner_agent_id=ResearchAgentId.CATALYST_EVENT,
        observed_at=NOW - dt.timedelta(minutes=5),
        as_of=evidence.as_of,
        universe_definition="NYSE common shares with point-in-time membership",
        universe_snapshot_id="nyse-universe-20260819",
        universe_observed_at=NOW - dt.timedelta(minutes=20),
        predictor_formula="standardized verified filing surprise",
        predictor_observed_at=NOW - dt.timedelta(minutes=10),
        target_matures_at=NOW - dt.timedelta(minutes=10) + dt.timedelta(days=2),
        coverage_fraction=0.97,
        source_refs=(evidence,),
    )


def hypothesis(*, kind: EvidenceKind = EvidenceKind.REAL) -> ImmutableHypothesis:
    observed = observation(kind=kind)
    use = EvidenceUse.RESEARCH if kind is EvidenceKind.REAL else EvidenceUse.WIRING_ONLY
    return ImmutableHypothesis(
        hypothesis_id="hypothesis-catalyst-001",
        parent_hypothesis_id=None,
        search_family_id="search-family-catalyst-001",
        agent_id=ResearchAgentId.CATALYST_EVENT,
        owner_family="strategy_research",
        lane_id="us_equities_research",
        created_at=NOW,
        created_by="catalyst-event-agent-v2",
        source_refs=observed.source_refs,
        evidence_hashes=(SHA_A,),
        evidence_use=use,
        observation=observed,
        point_in_time_policy="use only information available at predictor observation time",
        universe_definition=observed.universe_definition,
        universe_snapshot_id=observed.universe_snapshot_id,
        universe_observed_at=observed.universe_observed_at,
        instrument_scope="NYSE common shares",
        predictor_formula=observed.predictor_formula,
        sampling_timestamp=observed.predictor_observed_at,
        target_formula="two-session close-to-close excess return net of costs",
        target_horizon=TargetHorizon(duration=dt.timedelta(days=2)),
        target_matures_at=observed.target_matures_at,
        expected_direction=ExpectedDirection.POSITIVE,
        entry_rule="enter at first eligible open after the 15-minute maturity gate",
        exit_rule="exit at the second eligible session close",
        stop_rule="stop at preregistered adverse return boundary; stop wins same-bar collision",
        invalidation_rule="invalidate when catalyst timestamp or quote coverage is incomplete",
        economic_mechanism="verified surprises diffuse into prices with a bounded delay",
        alternative_explanations=("market beta", "sector momentum"),
        counterfactual_baseline="timestamp-matched sector-neutral non-event securities",
        baseline_id="sector-neutral-event-baseline-v1",
        cost_model_id="us-equity-cost-model-v3",
        slippage_model_id="spread-impact-model-v2",
        primary_metric="studentized bootstrap mean net excess return",
        secondary_metrics=("hit rate", "maximum adverse excursion"),
        falsification_rule="refute when the preregistered interval is entirely non-positive",
        free_parameters=(
            FreeParameter(name="surprise_z", candidate_values=(1.0, 1.5), lower_bound=1.0, upper_bound=1.5),
        ),
        search_budget=SearchBudget(max_parameter_combinations=2, max_attempts=2, max_cpu_seconds=60),
        minimum_observations=40,
        power_or_ci_gate="studentized bootstrap CI width <= 0.02 with >= 40 observations",
        multiple_testing_family="catalyst-surprise-family-2026q3",
        max_attempts=2,
        train_period=ResearchPeriod(start=NOW - dt.timedelta(days=180), end=NOW - dt.timedelta(days=91)),
        validation_period=ResearchPeriod(start=NOW - dt.timedelta(days=90), end=NOW - dt.timedelta(days=1)),
        holdout_period_sealed_ref=SealedHoldoutRef(
            seal_id="sealed-holdout-catalyst-2026q3",
            commitment_sha256=SHA_B,
            sealed_at=NOW - dt.timedelta(days=181),
            owner="science-kernel",
            access_policy="single reveal to independent reviewer only",
        ),
        holdout_access_policy="owner and generator receive sanitized terminal reasons only",
        model_hash=SHA_B,
        prompt_hash=SHA_C,
        protocol_version="strategy-research-v2",
        code_sha256=SHA_A,
        data_manifest_sha256=SHA_B,
        status=HypothesisStatus.PREREGISTERED,
        trading_authority=False,
        profitability_claim=False,
    )


__all__ = ("NOW", "SHA_A", "SHA_B", "SHA_C", "hypothesis", "observation", "source")
