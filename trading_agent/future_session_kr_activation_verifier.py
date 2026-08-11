from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_kr_ledger_identity import (
    experiment_ledger_v7_identity,
)
from trading_agent.future_session_kr_manifest import (
    KrFutureSessionPreparationManifest,
    canonical_kr_manifest_json,
)
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
)
from trading_agent.future_session_us_activation_verifier import (
    PRIVATE_EXECUTABLE_MODE,
    PRIVATE_FILE_MODE,
    read_private_file,
    verify_frozen_runtime,
    verify_private_directory,
)
from trading_agent.repository_current_main import (
    CurrentMainAuthorityError,
    current_main_commit,
)


@dataclass(frozen=True, slots=True)
class VerifiedKrActivation:
    label: str
    source_plist: Path
    installed_plist: Path
    receipt_path: Path
    manifest_sha256: str


def verify_kr_future_session_activation(
    *,
    manifest_path: Path,
    launch_agents_dir: Path,
) -> VerifiedKrActivation:
    payload = read_private_file(manifest_path, PRIVATE_FILE_MODE)
    try:
        manifest = KrFutureSessionPreparationManifest.model_validate_json(payload)
    except (TypeError, ValidationError, ValueError):
        raise FutureSessionActivationError("invalid_manifest") from None
    if canonical_kr_manifest_json(manifest).encode() != payload:
        raise FutureSessionActivationError("invalid_manifest")
    _verify_bound_authority_files(manifest)
    root = manifest_path.parent
    if manifest_path != root / "preparation-manifest.json":
        raise FutureSessionActivationError("noncanonical_manifest_path")
    for directory in (root, root / "jobs", root / "receipts", root / "logs"):
        verify_private_directory(directory)
    try:
        current_main = current_main_commit(manifest.authority_repository)
    except CurrentMainAuthorityError:
        raise FutureSessionActivationError("current_main_authority_invalid") from None
    if current_main != manifest.scheduler_main_sha:
        raise FutureSessionActivationError("current_main_authority_invalid")
    verify_frozen_runtime(manifest.frozen_runtime, manifest.runtime_commit_sha)
    if (
        not manifest.runtime_interpreter.is_file()
        or experiment_ledger_v7_identity(manifest.experiment_ledger) != manifest.experiment_ledger_identity_sha256
    ):
        raise FutureSessionActivationError("kr_runtime_authority_invalid")
    entry = manifest.entry
    expected = (
        root / "jobs" / "kr-supervisor.payload.zsh",
        root / "jobs" / "kr-supervisor.persistent.zsh",
        root / "jobs" / "kr-supervisor.plist",
        root / "receipts" / "kr-supervisor.json",
        root / "logs" / "kr-supervisor.stdout.log",
        root / "logs" / "kr-supervisor.stderr.log",
    )
    if expected != (
        entry.payload_wrapper,
        entry.persistent_wrapper,
        entry.persistent_plist,
        entry.receipt,
        entry.stdout_log,
        entry.stderr_log,
    ):
        raise FutureSessionActivationError("noncanonical_artifact_path")
    rendered_payload = read_private_file(entry.payload_wrapper, PRIVATE_EXECUTABLE_MODE)
    rendered_wrapper = read_private_file(entry.persistent_wrapper, PRIVATE_EXECUTABLE_MODE)
    rendered_plist = read_private_file(entry.persistent_plist, PRIVATE_FILE_MODE)
    if (
        hashlib.sha256(rendered_payload).hexdigest() != entry.payload_sha256
        or hashlib.sha256(rendered_wrapper).hexdigest() != entry.persistent_wrapper_sha256
        or hashlib.sha256(rendered_plist).hexdigest() != entry.persistent_plist_sha256
    ):
        raise FutureSessionActivationError("artifact_hash_mismatch")
    installed = launch_agents_dir / f"{entry.label}.plist"
    binding = f"readonly persistent_plist={shlex.quote(str(installed))}\n".encode()
    epoch_binding = " ".join(str(value) for value in manifest.internal_phase_epochs).encode()
    if binding not in rendered_wrapper or epoch_binding not in rendered_payload:
        raise FutureSessionActivationError("artifact_binding_invalid")
    return VerifiedKrActivation(
        label=entry.label,
        source_plist=entry.persistent_plist,
        installed_plist=installed,
        receipt_path=root / "activation-receipt.json",
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
    )


def verify_kr_supervisor_preflight(manifest_path: Path) -> None:
    manifest = _verify_kr_supervisor_runtime_preflight(manifest_path)
    if experiment_ledger_v7_identity(manifest.experiment_ledger) != manifest.experiment_ledger_identity_sha256:
        raise FutureSessionActivationError("bound_authority_changed")


def verify_kr_supervisor_restart_preflight(manifest_path: Path) -> None:
    manifest = _verify_kr_supervisor_runtime_preflight(manifest_path)
    _ = experiment_ledger_v7_identity(manifest.experiment_ledger)


def _verify_kr_supervisor_runtime_preflight(
    manifest_path: Path,
) -> KrFutureSessionPreparationManifest:
    payload = read_private_file(manifest_path, PRIVATE_FILE_MODE)
    try:
        manifest = KrFutureSessionPreparationManifest.model_validate_json(payload)
    except (TypeError, ValidationError, ValueError):
        raise FutureSessionActivationError("invalid_manifest") from None
    if canonical_kr_manifest_json(manifest).encode() != payload:
        raise FutureSessionActivationError("invalid_manifest")
    _verify_bound_authority_files(manifest)
    try:
        current_main = current_main_commit(manifest.authority_repository)
    except CurrentMainAuthorityError:
        raise FutureSessionActivationError("current_main_authority_invalid") from None
    if current_main != manifest.scheduler_main_sha:
        raise FutureSessionActivationError("bound_authority_changed")
    verify_frozen_runtime(manifest.frozen_runtime, manifest.runtime_commit_sha)
    entry = manifest.entry
    rendered_payload = read_private_file(entry.payload_wrapper, PRIVATE_EXECUTABLE_MODE)
    rendered_wrapper = read_private_file(entry.persistent_wrapper, PRIVATE_EXECUTABLE_MODE)
    rendered_plist = read_private_file(entry.persistent_plist, PRIVATE_FILE_MODE)
    if (
        hashlib.sha256(rendered_payload).hexdigest() != entry.payload_sha256
        or hashlib.sha256(rendered_wrapper).hexdigest() != entry.persistent_wrapper_sha256
        or hashlib.sha256(rendered_plist).hexdigest() != entry.persistent_plist_sha256
        or len(manifest.internal_phase_epochs) != 6
        or not manifest.runtime_interpreter.is_file()
        or not (manifest.authority_repository / "run_future_session_materialize.py").is_file()
    ):
        raise FutureSessionActivationError("invalid_internal_phase_count")
    return manifest


def _verify_bound_authority_files(
    manifest: KrFutureSessionPreparationManifest,
) -> None:
    request_payload = read_private_file(manifest.request_file, PRIVATE_FILE_MODE)
    plan_payload = read_private_file(manifest.plan_file, PRIVATE_FILE_MODE)
    if (
        hashlib.sha256(request_payload).hexdigest() != manifest.request_sha256
        or hashlib.sha256(plan_payload).hexdigest() != manifest.canonical_plan_file_sha256
        or manifest.plan_sha256.encode() not in plan_payload
    ):
        raise FutureSessionActivationError("authority_file_hash_mismatch")


__all__ = (
    "VerifiedKrActivation",
    "verify_kr_future_session_activation",
    "verify_kr_supervisor_preflight",
    "verify_kr_supervisor_restart_preflight",
)
