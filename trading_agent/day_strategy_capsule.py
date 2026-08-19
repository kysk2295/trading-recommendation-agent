from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

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

_ISSUER = object()


@dataclass(frozen=True, slots=True)
class _IssuedProof:
    issuer: object
    capsule_object_identity: int
    capsule_payload_sha256: str


class VerifiedStrategyCapsule:
    """Host-issued publication capability bound to one exact capsule object and payload."""

    __slots__ = ("_capsule", "_proof")

    def __init__(self, capsule: StrategyCapsule, proof: _IssuedProof) -> None:
        if not _proof_matches_capsule(proof, capsule):
            raise InvalidStrategyCapsuleError("strategy_capsule_verification_not_issued")
        object.__setattr__(self, "_capsule", capsule)
        object.__setattr__(self, "_proof", proof)

    def __setattr__(self, name: str, value: StrategyCapsule | _IssuedProof) -> None:
        del name, value
        raise AttributeError("verified strategy capsule is frozen")

    @property
    def capsule(self) -> StrategyCapsule:
        return self._capsule


def verified_strategy_capsule_payload(verified: VerifiedStrategyCapsule) -> StrategyCapsule:
    try:
        proof = verified._proof
        capsule = verified._capsule
    except AttributeError:
        raise InvalidStrategyCapsuleError("strategy_capsule_verification_not_issued") from None
    if type(verified) is not VerifiedStrategyCapsule or not _proof_matches_capsule(proof, capsule):
        raise InvalidStrategyCapsuleError("strategy_capsule_verification_not_issued")
    try:
        return StrategyCapsule.model_validate(capsule.model_dump(mode="python"))
    except ValueError:
        raise InvalidStrategyCapsuleError("strategy_capsule_verification_not_issued") from None


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
        )
    )


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
) -> VerifiedStrategyCapsule:
    if artifact_kind is CapsuleArtifactKind.GENERATED_PYTHON and (
        protocol_sha256 != generated_protocol_bundle_sha256()
        or evaluator_sha256 != generated_evaluator_bundle_sha256()
    ):
        raise InvalidStrategyCapsuleError("generated_capsule_host_bundle_mismatch")
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
    capsule = StrategyCapsule.model_validate(
        payload | {"capsule_id": StrategyCapsule.canonical_id_for(payload)}
    )
    return VerifiedStrategyCapsule(capsule, _issued_proof(capsule))


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
    sandbox: GeneratedStrategySandbox, published: PublishedGeneratedStrategy, bars: tuple[BarInput, ...]
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


def _source_bundle_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        source = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
    return digest.hexdigest()


def _issued_proof(capsule: StrategyCapsule) -> _IssuedProof:
    return _IssuedProof(
        issuer=_ISSUER,
        capsule_object_identity=id(capsule),
        capsule_payload_sha256=_capsule_payload_sha256(capsule),
    )


def _proof_matches_capsule(proof: _IssuedProof, capsule: StrategyCapsule) -> bool:
    return (
        proof.issuer is _ISSUER
        and proof.capsule_object_identity == id(capsule)
        and proof.capsule_payload_sha256 == _capsule_payload_sha256(capsule)
    )


def _capsule_payload_sha256(capsule: StrategyCapsule) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(capsule).encode()).hexdigest()
