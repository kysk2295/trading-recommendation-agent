from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.day_strategy_capsule_support import (
    PUBLISHED_AT,
    SHA_A,
    SHA_B,
)
from tests.day_strategy_capsule_support import (
    bar as _bar,
)
from tests.day_strategy_capsule_support import (
    build_generated_capsule as _build_generated_capsule,
)
from tests.day_strategy_capsule_support import (
    builtin_capsule as _builtin_capsule,
)
from tests.day_strategy_capsule_support import (
    no_signal_source as _no_signal_source,
)
from tests.day_strategy_capsule_support import (
    nondeterministic_source as _nondeterministic_source,
)
from tests.day_strategy_capsule_support import (
    proposal as _proposal,
)
from trading_agent.day_strategy_capsule_models import (
    CapsuleAuthorityCeiling,
    CapsulePreflightReceipt,
    CapsuleResourceLimits,
    InvalidStrategyCapsuleError,
    StrategyCapsule,
)
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.research_identity_models import MarketId


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


def test_generated_capsule_rejects_caller_supplied_placeholder_host_hashes(tmp_path: Path) -> None:
    # Given: a real artifact and sandbox paired with caller-invented protocol/evaluator hashes.
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    store = GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)
    published = store.publish(_proposal(_no_signal_source()))
    limits = CapsuleResourceLimits()
    sandbox = GeneratedStrategySandbox(runtime, tmp_path / "tasks", limits.to_generated_limits())

    # When/Then: real sandbox success cannot bless hashes that do not identify host code.
    with pytest.raises(InvalidStrategyCapsuleError, match="generated_capsule_host_bundle_mismatch"):
        _ = _build_generated_capsule(
            published.artifact.artifact_id,
            published.artifact.payload.source_sha256,
            store,
            sandbox,
            limits,
            (_bar(),),
            protocol_sha256=SHA_B,
            evaluator_sha256=SHA_A,
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
        _ = StrategyCapsule.model_validate(capsule.model_dump(mode="python") | {"preflight_receipt": forged})
