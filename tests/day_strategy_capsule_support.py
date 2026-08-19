from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from trading_agent.day_hypothesis_models import CostModelDeclaration
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    build_strategy_capsule,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
)
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    CapsuleResourceLimits,
    StrategyCapsule,
)
from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import HypothesisRegistration, ResearchHypothesisCard
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.models import BarInput
from trading_agent.research_hypothesis_registration import load_research_hypothesis_manifest
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import CandidateStrategyDraft, LlmCallReceipt, ProposedHypothesis

SHA_A = "a" * 64
SHA_B = "b" * 64
PUBLISHED_AT = dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)
PROJECT = Path(__file__).resolve().parents[1]


def builtin_capsule(
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    authority_ceiling: CapsuleAuthorityCeiling = CapsuleAuthorityCeiling.RESEARCH_ONLY,
) -> StrategyCapsule:
    return build_strategy_capsule(builtin_request(market_id=market_id, authority_ceiling=authority_ceiling))


def builtin_request(
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    authority_ceiling: CapsuleAuthorityCeiling = CapsuleAuthorityCeiling.RESEARCH_ONLY,
) -> DayStrategyCapsuleRequest:
    return DayStrategyCapsuleRequest(
        hypothesis_version_id=SHA_A,
        attempt_binding_id=SHA_B,
        market_id=market_id,
        artifact_kind=CapsuleArtifactKind.BUILTIN,
        artifact_ref=f"artifact://safe/{SHA_A}",
        artifact_sha256=SHA_A,
        generated_artifact_id=None,
        evaluation_cadence="each_completed_bar",
        evidence_schema=("completed_bar_v1", "fresh_spread_v1"),
        entry_rule="enter_on_host_validated_candidate",
        exit_rule="exit_at_preregistered_horizon",
        stop_rule="stop_first_on_same_bar_collision",
        target_rule="host_projects_preregistered_targets",
        cost_model=CostModelDeclaration(
            model_id="us_equities_cost_v1",
            commission_bps=Decimal("1"),
            slippage_bps=Decimal("2"),
        ),
        slippage_model_id="bounded_intraday_slippage_v1",
        resource_limits=CapsuleResourceLimits(),
        risk_policy_ref="risk-policy://day-research/v1",
        protocol_version=1,
        protocol_sha256=SHA_B,
        evaluator_sha256=SHA_A,
        published_at=PUBLISHED_AT,
        authority_ceiling=authority_ceiling,
    )


def build_generated_capsule(
    generated_artifact_id: str,
    artifact_sha256: str,
    store: GeneratedStrategyArtifactStore | None,
    sandbox: GeneratedStrategySandbox,
    limits: CapsuleResourceLimits,
    bars: tuple[BarInput, ...],
    *,
    protocol_sha256: str | None = None,
    evaluator_sha256: str | None = None,
) -> StrategyCapsule:
    return build_strategy_capsule(
        DayStrategyCapsuleRequest(
            hypothesis_version_id=SHA_A,
            attempt_binding_id=SHA_B,
            market_id=MarketId.US_EQUITIES,
            artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
            artifact_ref=f"artifact://safe/{artifact_sha256}",
            artifact_sha256=artifact_sha256,
            generated_artifact_id=generated_artifact_id,
            evaluation_cadence="each_completed_bar",
            evidence_schema=("completed_bar_v1",),
            entry_rule="host_validates_candidate",
            exit_rule="host_exits",
            stop_rule="host_stop_first",
            target_rule="host_targets",
            cost_model=CostModelDeclaration(
                model_id="us_equities_cost_v1",
                commission_bps=Decimal("1"),
                slippage_bps=Decimal("2"),
            ),
            slippage_model_id="bounded_intraday_slippage_v1",
            resource_limits=limits,
            risk_policy_ref="risk-policy://day-research/v1",
            protocol_version=1,
            protocol_sha256=(generated_protocol_bundle_sha256() if protocol_sha256 is None else protocol_sha256),
            evaluator_sha256=(generated_evaluator_bundle_sha256() if evaluator_sha256 is None else evaluator_sha256),
            published_at=PUBLISHED_AT,
            authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
            generated_verification=(None if store is None else GeneratedCapsuleVerification(store, sandbox, bars)),
        )
    )


def proposal(source: str) -> ProposedHypothesis:
    manifest = load_research_hypothesis_manifest(PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json")
    scope = ExperimentScope.model_validate(manifest.experiment_scope.model_dump(mode="python"))
    registration = HypothesisRegistration(
        hypothesis_id=scope.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        primary_lane=scope.primary_lane,
        hypothesis=manifest.hypothesis,
        falsification_rule=manifest.falsification_rule,
        source_registered_at=scope.registered_at,
        ledger_recorded_at=scope.registered_at,
    )
    card = ResearchHypothesisCard(
        hypothesis=registration,
        research_source_keys=tuple(sorted(str(research_source_key(item)) for item in manifest.research_sources)),
        economic_mechanism=manifest.economic_mechanism,
        counterfactual_baseline=manifest.counterfactual_baseline,
    )
    return ProposedHypothesis(
        card=card,
        cited_sources=manifest.research_sources,
        llm_receipt=LlmCallReceipt(
            model_id="fixture-researcher-v1",
            prompt_sha256=SHA_A,
            response_sha256=SHA_B,
            seed=7,
            temperature=0.0,
            called_at=PUBLISHED_AT - dt.timedelta(minutes=1),
        ),
        strategy_draft=CandidateStrategyDraft(source, ()),
    )


def bar() -> BarInput:
    return BarInput(
        "TEST",
        PUBLISHED_AT - dt.timedelta(minutes=5),
        10.0,
        11.0,
        9.5,
        10.5,
        100_000,
        9.8,
        1_000_000,
        20.0,
    )


def no_signal_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return None\n"
        "    return Strategy()\n"
    )


def nondeterministic_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    import time\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['low'], 'rationale': str(time.time_ns())}\n"
        "    return Strategy()\n"
    )
