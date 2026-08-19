from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from trading_agent.day_hypothesis_models import CostModelDeclaration
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    CapsulePreflightReceipt,
    CapsuleResourceLimits,
    InvalidStrategyCapsuleError,
    StrategyCapsule,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_protocol import observe_request
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.generated_strategy_source import GeneratedStrategySourceSnapshot
from trading_agent.models import BarInput
from trading_agent.research_identity_models import MarketId


@dataclass(frozen=True, slots=True)
class GeneratedCapsuleVerification:
    artifact_store: GeneratedStrategyArtifactStore
    sandbox: GeneratedStrategySandbox
    replay_bars: tuple[BarInput, ...]


@dataclass(frozen=True, slots=True)
class DayStrategyCapsuleRequest:
    hypothesis_version_id: str
    attempt_binding_id: str
    market_id: MarketId
    artifact_kind: CapsuleArtifactKind
    artifact_ref: str
    artifact_sha256: str
    generated_artifact_id: str | None
    evaluation_cadence: str
    evidence_schema: tuple[str, ...]
    entry_rule: str
    exit_rule: str
    stop_rule: str
    target_rule: str
    cost_model: CostModelDeclaration
    slippage_model_id: str
    resource_limits: CapsuleResourceLimits
    risk_policy_ref: str
    protocol_version: int
    protocol_sha256: str
    evaluator_sha256: str
    published_at: dt.datetime
    authority_ceiling: CapsuleAuthorityCeiling
    generated_verification: GeneratedCapsuleVerification | None = None


def generated_protocol_bundle_sha256() -> str:
    return _source_bundle_sha256(
        (
            Path(__file__).with_name("generated_strategy_protocol.py"),
            Path(__file__).with_name("generated_strategy_runner.py"),
            Path(__file__).with_name("generated_strategy_session.py"),
        )
    )


def generated_evaluator_bundle_sha256() -> str:
    return _source_bundle_sha256(
        (
            Path(__file__),
            Path(__file__).with_name("day_strategy_capsule_models.py"),
            Path(__file__).with_name("generated_strategy_artifact.py"),
            Path(__file__).with_name("generated_strategy_execution.py"),
            Path(__file__).with_name("generated_strategy_runtime.py"),
            Path(__file__).with_name("generated_strategy_sandbox.py"),
            Path(__file__).with_name("generated_strategy_source.py"),
        )
    )


def build_strategy_capsule(request: DayStrategyCapsuleRequest) -> StrategyCapsule:
    if request.artifact_kind is CapsuleArtifactKind.GENERATED_PYTHON and (
        request.protocol_sha256 != generated_protocol_bundle_sha256()
        or request.evaluator_sha256 != generated_evaluator_bundle_sha256()
    ):
        raise InvalidStrategyCapsuleError("generated_capsule_host_bundle_mismatch")
    preflight_receipt = _preflight_generated_artifact(request)
    payload = {
        "capsule_id": "",
        "hypothesis_version_id": request.hypothesis_version_id,
        "attempt_binding_id": request.attempt_binding_id,
        "market_id": request.market_id,
        "artifact_kind": request.artifact_kind,
        "artifact_ref": request.artifact_ref,
        "artifact_sha256": request.artifact_sha256,
        "generated_artifact_id": request.generated_artifact_id,
        "evaluation_cadence": request.evaluation_cadence,
        "evidence_schema": request.evidence_schema,
        "entry_rule": request.entry_rule,
        "exit_rule": request.exit_rule,
        "stop_rule": request.stop_rule,
        "target_rule": request.target_rule,
        "cost_model": request.cost_model,
        "slippage_model_id": request.slippage_model_id,
        "resource_limits": request.resource_limits,
        "risk_policy_ref": request.risk_policy_ref,
        "protocol_version": request.protocol_version,
        "protocol_sha256": request.protocol_sha256,
        "evaluator_sha256": request.evaluator_sha256,
        "preflight_receipt": preflight_receipt,
        "published_at": request.published_at,
        "authority_ceiling": request.authority_ceiling,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return StrategyCapsule.model_validate(payload | {"capsule_id": StrategyCapsule.canonical_id_for(payload)})


def publish_day_strategy_capsule(
    store: ExperimentLedgerStore,
    request: DayStrategyCapsuleRequest | StrategyCapsule,
) -> tuple[StrategyCapsule, bool]:
    """Build and persist in the host, whose modules are unavailable to generated sandboxes.

    Same-process private-field or raw-SQL access is outside this public API boundary.
    """
    match request:
        case StrategyCapsule():
            raise InvalidStrategyCapsuleError("raw_strategy_capsule_publication_forbidden")
        case DayStrategyCapsuleRequest():
            capsule = build_strategy_capsule(request)
        case unreachable:
            assert_never(unreachable)
    with store.writer() as writer:
        created = writer._register_day_strategy_capsule(capsule)
    return capsule, created


def _preflight_generated_artifact(request: DayStrategyCapsuleRequest) -> CapsulePreflightReceipt | None:
    match request.artifact_kind:
        case CapsuleArtifactKind.BUILTIN:
            if request.generated_artifact_id is not None or request.generated_verification is not None:
                raise InvalidStrategyCapsuleError("builtin_capsule_generated_preflight_forbidden")
            return None
        case CapsuleArtifactKind.GENERATED_PYTHON:
            verification = request.generated_verification
            if (
                request.generated_artifact_id is None
                or verification is None
                or not verification.replay_bars
                or request.protocol_version != 1
                or verification.sandbox.runtime != verification.artifact_store.runtime
                or verification.sandbox.limits != request.resource_limits.to_generated_limits()
            ):
                raise InvalidStrategyCapsuleError("generated_capsule_preflight_required")
            artifact = verification.artifact_store.load(request.generated_artifact_id)
            if artifact.payload.source_sha256 != request.artifact_sha256:
                raise InvalidStrategyCapsuleError("generated_capsule_artifact_mismatch")
            published = PublishedGeneratedStrategy(
                artifact=artifact,
                source_path=verification.artifact_store.root / request.generated_artifact_id / "strategy.py",
                manifest_path=verification.artifact_store.root / request.generated_artifact_id / "manifest.json",
                created=False,
            )
            snapshot = verification.sandbox.capture_source(published)
            first_digest = _replay_digest(verification.sandbox, snapshot, verification.replay_bars)
            second_digest = _replay_digest(verification.sandbox, snapshot, verification.replay_bars)
            if first_digest != second_digest:
                raise InvalidStrategyCapsuleError("generated_capsule_replay_nondeterministic")
            receipt_payload = {
                "receipt_id": "",
                "generated_artifact_id": request.generated_artifact_id,
                "runtime_fingerprint": artifact.payload.runtime.runtime_fingerprint,
                "sandbox_profile_version": artifact.payload.runtime.sandbox_profile_version,
                "protocol_version": request.protocol_version,
                "protocol_sha256": request.protocol_sha256,
                "evaluator_sha256": request.evaluator_sha256,
                "resource_limits": request.resource_limits,
                "replay_input_sha256": _replay_input_digest(verification.replay_bars),
                "first_run_sha256": first_digest,
                "second_run_sha256": second_digest,
                "deterministic_replay_sha256": hashlib.sha256(f"{first_digest}:{second_digest}".encode()).hexdigest(),
                "successful": True,
                "completed_at": request.published_at,
                "trading_authority": False,
            }
            return CapsulePreflightReceipt.model_validate(
                receipt_payload | {"receipt_id": CapsulePreflightReceipt.canonical_id_for(receipt_payload)}
            )
        case unreachable:
            assert_never(unreachable)


def _replay_digest(
    sandbox: GeneratedStrategySandbox,
    snapshot: GeneratedStrategySourceSnapshot,
    bars: tuple[BarInput, ...],
) -> str:
    with sandbox.open_source_session(snapshot) as session:
        for bar in bars:
            _ = session.observe(bar, None)
        return session.signal_stream_sha256


def _replay_input_digest(bars: tuple[BarInput, ...]) -> str:
    frames = tuple(
        canonical_experiment_ledger_json(observe_request(sequence, bar, None))
        for sequence, bar in enumerate(bars, start=1)
    )
    return hashlib.sha256("".join(frames).encode()).hexdigest()


def _source_bundle_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        source = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
    return digest.hexdigest()
