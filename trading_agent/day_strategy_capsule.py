from __future__ import annotations

import datetime as dt
import hashlib

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
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_protocol import observe_request
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.models import BarInput
from trading_agent.research_identity_models import MarketId


def build_strategy_capsule(
    *,
    hypothesis_version_id: str,
    attempt_binding_id: str,
    market_id: MarketId,
    artifact_kind: CapsuleArtifactKind,
    artifact_ref: str,
    artifact_sha256: str,
    generated_artifact_id: str | None,
    evaluation_cadence: str,
    evidence_schema: tuple[str, ...],
    entry_rule: str,
    exit_rule: str,
    stop_rule: str,
    target_rule: str,
    cost_model: CostModelDeclaration,
    slippage_model_id: str,
    resource_limits: CapsuleResourceLimits,
    risk_policy_ref: str,
    protocol_version: int,
    protocol_sha256: str,
    evaluator_sha256: str,
    published_at: dt.datetime,
    authority_ceiling: CapsuleAuthorityCeiling,
    generated_artifact_store: GeneratedStrategyArtifactStore | None = None,
    generated_sandbox: GeneratedStrategySandbox | None = None,
    replay_bars: tuple[BarInput, ...] = (),
) -> StrategyCapsule:
    preflight_receipt = _preflight_generated_artifact(
        artifact_kind=artifact_kind,
        artifact_sha256=artifact_sha256,
        generated_artifact_id=generated_artifact_id,
        resource_limits=resource_limits,
        protocol_version=protocol_version,
        protocol_sha256=protocol_sha256,
        evaluator_sha256=evaluator_sha256,
        completed_at=published_at,
        artifact_store=generated_artifact_store,
        sandbox=generated_sandbox,
        replay_bars=replay_bars,
    )
    payload = {
        "capsule_id": "",
        "hypothesis_version_id": hypothesis_version_id,
        "attempt_binding_id": attempt_binding_id,
        "market_id": market_id,
        "artifact_kind": artifact_kind,
        "artifact_ref": artifact_ref,
        "artifact_sha256": artifact_sha256,
        "generated_artifact_id": generated_artifact_id,
        "evaluation_cadence": evaluation_cadence,
        "evidence_schema": evidence_schema,
        "entry_rule": entry_rule,
        "exit_rule": exit_rule,
        "stop_rule": stop_rule,
        "target_rule": target_rule,
        "cost_model": cost_model,
        "slippage_model_id": slippage_model_id,
        "resource_limits": resource_limits,
        "risk_policy_ref": risk_policy_ref,
        "protocol_version": protocol_version,
        "protocol_sha256": protocol_sha256,
        "evaluator_sha256": evaluator_sha256,
        "preflight_receipt": preflight_receipt,
        "published_at": published_at,
        "authority_ceiling": authority_ceiling,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return StrategyCapsule.model_validate(payload | {"capsule_id": StrategyCapsule.canonical_id_for(payload)})


def _preflight_generated_artifact(
    *,
    artifact_kind: CapsuleArtifactKind,
    artifact_sha256: str,
    generated_artifact_id: str | None,
    resource_limits: CapsuleResourceLimits,
    protocol_version: int,
    protocol_sha256: str,
    evaluator_sha256: str,
    completed_at: dt.datetime,
    artifact_store: GeneratedStrategyArtifactStore | None,
    sandbox: GeneratedStrategySandbox | None,
    replay_bars: tuple[BarInput, ...],
) -> CapsulePreflightReceipt | None:
    match artifact_kind:
        case CapsuleArtifactKind.BUILTIN:
            if generated_artifact_id is not None or artifact_store is not None or sandbox is not None or replay_bars:
                raise InvalidStrategyCapsuleError("builtin_capsule_generated_preflight_forbidden")
            return None
        case CapsuleArtifactKind.GENERATED_PYTHON:
            if (
                generated_artifact_id is None
                or artifact_store is None
                or sandbox is None
                or not replay_bars
                or protocol_version != 1
                or sandbox.runtime != artifact_store.runtime
                or sandbox.limits != resource_limits.to_generated_limits()
            ):
                raise InvalidStrategyCapsuleError("generated_capsule_preflight_required")
            artifact = artifact_store.load(generated_artifact_id)
            if artifact.payload.source_sha256 != artifact_sha256:
                raise InvalidStrategyCapsuleError("generated_capsule_artifact_mismatch")
            published = PublishedGeneratedStrategy(
                artifact=artifact,
                source_path=artifact_store.root / generated_artifact_id / "strategy.py",
                manifest_path=artifact_store.root / generated_artifact_id / "manifest.json",
                created=False,
            )
            first_digest = _replay_digest(sandbox, published, replay_bars)
            second_digest = _replay_digest(sandbox, published, replay_bars)
            if first_digest != second_digest:
                raise InvalidStrategyCapsuleError("generated_capsule_replay_nondeterministic")
            receipt_payload = {
                "receipt_id": "",
                "generated_artifact_id": generated_artifact_id,
                "runtime_fingerprint": artifact.payload.runtime.runtime_fingerprint,
                "sandbox_profile_version": artifact.payload.runtime.sandbox_profile_version,
                "protocol_version": protocol_version,
                "protocol_sha256": protocol_sha256,
                "evaluator_sha256": evaluator_sha256,
                "resource_limits": resource_limits,
                "replay_input_sha256": _replay_input_digest(replay_bars),
                "first_run_sha256": first_digest,
                "second_run_sha256": second_digest,
                "deterministic_replay_sha256": hashlib.sha256(f"{first_digest}:{second_digest}".encode()).hexdigest(),
                "successful": True,
                "completed_at": completed_at,
                "trading_authority": False,
            }
            return CapsulePreflightReceipt.model_validate(
                receipt_payload | {"receipt_id": CapsulePreflightReceipt.canonical_id_for(receipt_payload)}
            )


def _replay_digest(
    sandbox: GeneratedStrategySandbox,
    published: PublishedGeneratedStrategy,
    bars: tuple[BarInput, ...],
) -> str:
    with sandbox.open_session(published) as session:
        for bar in bars:
            _ = session.observe(bar, None)
        return session.signal_stream_sha256


def _replay_input_digest(bars: tuple[BarInput, ...]) -> str:
    frames = tuple(
        canonical_experiment_ledger_json(observe_request(sequence, bar, None))
        for sequence, bar in enumerate(bars, start=1)
    )
    return hashlib.sha256("".join(frames).encode()).hexdigest()


__all__ = ("build_strategy_capsule",)
