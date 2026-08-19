from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.day_hypothesis_models import CostModelDeclaration
from trading_agent.day_strategy_capsule import build_strategy_capsule
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    CapsulePreflightReceipt,
    CapsuleResourceLimits,
    InvalidStrategyCapsuleError,
    StrategyCapsule,
)
from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import HypothesisRegistration, ResearchHypothesisCard
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
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


def _builtin_capsule(
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    authority_ceiling: CapsuleAuthorityCeiling = CapsuleAuthorityCeiling.RESEARCH_ONLY,
) -> StrategyCapsule:
    return build_strategy_capsule(
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


def test_builtin_capsule_has_content_addressed_declarative_identity() -> None:
    # Given/When: the same declaration is built twice.
    first = _builtin_capsule()
    second = _builtin_capsule()

    # Then: identity is exact and the capsule carries declarations only.
    assert first == second
    assert first.capsule_id == StrategyCapsule.canonical_id_for(first.model_dump(mode="python"))
    assert first.trading_authority is False
    assert first.profitability_claim is False
    assert "provider" not in StrategyCapsule.model_fields
    assert "broker" not in StrategyCapsule.model_fields
    assert "order" not in StrategyCapsule.model_fields


def test_kr_capsule_cannot_be_paper_capable() -> None:
    # Given/When/Then: the KR lane cannot declare a US paper ceiling.
    with pytest.raises(ValidationError, match="kr_capsule_authority_ceiling"):
        _ = _builtin_capsule(
            market_id=MarketId.KR_EQUITIES,
            authority_ceiling=CapsuleAuthorityCeiling.US_ALPACA_PAPER_CAPABLE,
        )


def test_capsule_rejects_stale_identity_and_extra_authority_fields() -> None:
    # Given: a valid immutable capsule.
    capsule = _builtin_capsule()

    # When/Then: validated copy and parse boundaries reject tampering and extra fields.
    with pytest.raises(ValidationError, match="capsule_id_mismatch"):
        _ = capsule.model_copy(update={"exit_rule": "changed"})
    with pytest.raises(ValidationError):
        _ = StrategyCapsule.model_validate(capsule.model_dump() | {"current_authority": True})


def test_capsule_publication_timestamp_is_normalized_to_utc() -> None:
    # Given/When: an equivalent non-UTC instant crosses the builder boundary.
    capsule = _builtin_capsule().model_copy(
        update={"published_at": PUBLISHED_AT.astimezone(dt.timezone(dt.timedelta(hours=9)))}
    )

    # Then: the authoritative declaration is UTC and retains the same identity.
    assert capsule.published_at.tzinfo is dt.UTC
    assert capsule == _builtin_capsule()


def test_generated_capsule_requires_real_deterministic_two_run_preflight(tmp_path: Path) -> None:
    # Given: an immutable generated artifact and its deny-default sandbox.
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    store = GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)
    published = store.publish(_proposal(_no_signal_source()))
    limits = CapsuleResourceLimits()
    sandbox = GeneratedStrategySandbox(runtime, tmp_path / "tasks", limits.to_generated_limits())
    bar = _bar()

    # When: the builder verifies the stored artifact and replays the completed bar twice.
    capsule = _build_generated_capsule(
        published.artifact.artifact_id,
        published.artifact.payload.source_sha256,
        store,
        sandbox,
        limits,
        (bar,),
    )

    # Then: the successful receipt binds the exact runtime, limits, inputs, and equal run digests.
    receipt = capsule.preflight_receipt
    assert receipt is not None
    assert receipt.successful is True
    assert receipt.first_run_sha256 == receipt.second_run_sha256
    assert receipt.runtime_fingerprint == runtime.runtime_fingerprint
    assert receipt.replay_input_sha256 == "a862bdcbe5de3e85175a33140a40dad701cece1f7e9ae4e61d4daf1f7480c94d"
    assert capsule.generated_artifact_id == published.artifact.artifact_id


def test_generated_capsule_rejects_missing_or_mismatched_preflight_context(tmp_path: Path) -> None:
    # Given: one real artifact with a runtime-bound sandbox and a different empty artifact store.
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    store = GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)
    published = store.publish(_proposal(_no_signal_source()))
    limits = CapsuleResourceLimits()
    sandbox = GeneratedStrategySandbox(runtime, tmp_path / "tasks", limits.to_generated_limits())
    empty_store = GeneratedStrategyArtifactStore(tmp_path / "other-artifacts", runtime)

    # When/Then: absent proof context, wrong store, wrong limits, and wrong source hash all fail closed.
    with pytest.raises(InvalidStrategyCapsuleError, match="generated_capsule_preflight_required"):
        _ = _build_generated_capsule(
            published.artifact.artifact_id,
            published.artifact.payload.source_sha256,
            None,
            sandbox,
            limits,
            (_bar(),),
        )
    with pytest.raises(GeneratedStrategyArtifactError, match="load_failed"):
        _ = _build_generated_capsule(
            published.artifact.artifact_id,
            published.artifact.payload.source_sha256,
            empty_store,
            sandbox,
            limits,
            (_bar(),),
        )
    with pytest.raises(InvalidStrategyCapsuleError, match="generated_capsule_preflight_required"):
        _ = _build_generated_capsule(
            published.artifact.artifact_id,
            published.artifact.payload.source_sha256,
            store,
            sandbox,
            limits.model_copy(update={"wall_seconds": 3.0}),
            (_bar(),),
        )
    with pytest.raises(InvalidStrategyCapsuleError, match="generated_capsule_artifact_mismatch"):
        _ = _build_generated_capsule(
            published.artifact.artifact_id,
            SHA_A,
            store,
            sandbox,
            limits,
            (_bar(),),
        )


def test_generated_capsule_rejects_nondeterministic_two_run_output(tmp_path: Path) -> None:
    # Given: generated source whose validated candidate rationale changes with wall-clock time.
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    store = GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)
    published = store.publish(_proposal(_nondeterministic_source()))
    limits = CapsuleResourceLimits()
    sandbox = GeneratedStrategySandbox(runtime, tmp_path / "tasks", limits.to_generated_limits())

    # When/Then: two real sandbox runs producing different frame streams cannot publish a receipt.
    with pytest.raises(InvalidStrategyCapsuleError, match="generated_capsule_replay_nondeterministic"):
        _ = _build_generated_capsule(
            published.artifact.artifact_id,
            published.artifact.payload.source_sha256,
            store,
            sandbox,
            limits,
            (_bar(),),
        )


def test_nested_preflight_receipt_is_revalidated_after_model_construct(tmp_path: Path) -> None:
    # Given: a valid generated capsule and a forged nested receipt bypassing normal construction.
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    store = GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)
    published = store.publish(_proposal(_no_signal_source()))
    limits = CapsuleResourceLimits()
    sandbox = GeneratedStrategySandbox(runtime, tmp_path / "tasks", limits.to_generated_limits())
    capsule = _build_generated_capsule(
        published.artifact.artifact_id,
        published.artifact.payload.source_sha256,
        store,
        sandbox,
        limits,
        (_bar(),),
    )
    receipt = capsule.preflight_receipt
    assert receipt is not None
    forged = CapsulePreflightReceipt.model_construct(
        receipt_id="f" * 64,
        generated_artifact_id=receipt.generated_artifact_id,
        runtime_fingerprint=receipt.runtime_fingerprint,
        sandbox_profile_version=receipt.sandbox_profile_version,
        protocol_version=receipt.protocol_version,
        protocol_sha256=receipt.protocol_sha256,
        evaluator_sha256=receipt.evaluator_sha256,
        resource_limits=receipt.resource_limits,
        replay_input_sha256=receipt.replay_input_sha256,
        first_run_sha256=receipt.first_run_sha256,
        second_run_sha256=receipt.second_run_sha256,
        deterministic_replay_sha256=receipt.deterministic_replay_sha256,
        successful=True,
        completed_at=receipt.completed_at,
        trading_authority=False,
    )

    # When/Then: the outer trust boundary revalidates and rejects the nested forged receipt.
    with pytest.raises(ValidationError):
        _ = StrategyCapsule.model_validate(
            capsule.model_dump(mode="python") | {"preflight_receipt": forged}
        )


def _build_generated_capsule(
    generated_artifact_id: str,
    artifact_sha256: str,
    store: GeneratedStrategyArtifactStore | None,
    sandbox: GeneratedStrategySandbox,
    limits: CapsuleResourceLimits,
    bars: tuple[BarInput, ...],
) -> StrategyCapsule:
    return build_strategy_capsule(
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
            model_id="us_equities_cost_v1", commission_bps=Decimal("1"), slippage_bps=Decimal("2")
        ),
        slippage_model_id="bounded_intraday_slippage_v1",
        resource_limits=limits,
        risk_policy_ref="risk-policy://day-research/v1",
        protocol_version=1,
        protocol_sha256=SHA_B,
        evaluator_sha256=SHA_A,
        published_at=PUBLISHED_AT,
        authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
        generated_artifact_store=store,
        generated_sandbox=sandbox,
        replay_bars=bars,
    )


def _proposal(source: str) -> ProposedHypothesis:
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


def _bar() -> BarInput:
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


def _no_signal_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return None\n"
        "    return Strategy()\n"
    )


def _nondeterministic_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    import time\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['low'], 'rationale': str(time.time_ns())}\n"
        "    return Strategy()\n"
    )
