from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import pytest

from tests.day_strategy_capsule_support import builtin_capsule as _builtin_capsule
from trading_agent.day_strategy_capsule import publish_day_strategy_capsule
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsulePreflightReceipt,
    InvalidStrategyCapsuleError,
    StrategyCapsule,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore


def test_capsule_module_exposes_no_forgeable_issuer_or_capability() -> None:
    # Given: the public capsule module loaded by arbitrary same-process caller code.
    module = importlib.import_module("trading_agent.day_strategy_capsule")

    # When: former issuer/capability construction surfaces are enumerated.
    exposed = tuple(name for name in ("_ISSUER", "_issued_proof", "VerifiedStrategyCapsule") if hasattr(module, name))

    # Then: object.__new__ and object.__setattr__ have no capability class or token to forge.
    assert exposed == ()


def test_writer_exposes_no_capsule_mutation_method(tmp_path: Path) -> None:
    # Given: a public experiment-ledger writer.
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")

    # When: its supported public methods are enumerated.
    with store.writer() as writer:
        public_methods = tuple(name for name in dir(writer) if not name.startswith("_"))

    # Then: raw models, canonical fake receipts, and forged objects have no public mutation path.
    assert "register_day_strategy_capsule" not in public_methods


def test_canonical_fake_receipt_has_no_public_ledger_mutation_path(tmp_path: Path) -> None:
    # Given: a canonical generated capsule assembled entirely from caller-controlled fields.
    builtin = _builtin_capsule()
    run_hash = "d" * 64
    receipt_payload = {
        "receipt_id": "",
        "generated_artifact_id": "c" * 64,
        "runtime_fingerprint": "e" * 64,
        "sandbox_profile_version": "generated_strategy_sandbox_v1",
        "protocol_version": 1,
        "protocol_sha256": builtin.protocol_sha256,
        "evaluator_sha256": builtin.evaluator_sha256,
        "resource_limits": builtin.resource_limits,
        "replay_input_sha256": "f" * 64,
        "first_run_sha256": run_hash,
        "second_run_sha256": run_hash,
        "deterministic_replay_sha256": hashlib.sha256(f"{run_hash}:{run_hash}".encode()).hexdigest(),
        "successful": True,
        "completed_at": builtin.published_at,
        "trading_authority": False,
    }
    receipt = CapsulePreflightReceipt.model_validate(
        receipt_payload | {"receipt_id": CapsulePreflightReceipt.canonical_id_for(receipt_payload)}
    )
    capsule_payload = builtin.model_dump(mode="python") | {
        "capsule_id": "",
        "artifact_kind": CapsuleArtifactKind.GENERATED_PYTHON,
        "generated_artifact_id": receipt.generated_artifact_id,
        "preflight_receipt": receipt,
    }
    forged = StrategyCapsule.model_validate(
        capsule_payload | {"capsule_id": StrategyCapsule.canonical_id_for(capsule_payload)}
    )
    object.__setattr__(forged, "_former_capability_proof", "caller-controlled")
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")

    # When: the caller passes the raw canonical object to the supported public boundary.
    with pytest.raises(InvalidStrategyCapsuleError, match="raw_strategy_capsule_publication_forbidden"):
        _ = publish_day_strategy_capsule(store, forged)

    # Then: neither the canonical model nor injected private state reaches persistence.
    assert store.day_strategy_capsules() == ()
