from __future__ import annotations

import hashlib
import os
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from trading_agent.future_session_materialization_models import (
    FutureSessionPreparationManifest,
    PreparedUsRoleArtifact,
    canonical_manifest_json,
)
from trading_agent.future_session_plan_models import FutureSessionUsRole
from trading_agent.future_session_us_activation_models import (
    ActivatedUsRoleArtifact,
    FutureSessionActivationError,
)
from trading_agent.repository_current_main import (
    CurrentMainAuthorityError,
    current_main_commit,
)

PRIVATE_FILE_MODE: Final = 0o600
PRIVATE_EXECUTABLE_MODE: Final = 0o700
PRIVATE_DIRECTORY_MODE: Final = 0o700


@dataclass(frozen=True, slots=True)
class VerifiedActivation:
    entries: tuple[ActivatedUsRoleArtifact, ...]
    manifest_sha256: str
    receipt_path: Path


def verify_us_future_session_activation(*, manifest_path: Path, launch_agents_dir: Path) -> VerifiedActivation:
    if not launch_agents_dir.is_absolute():
        raise FutureSessionActivationError("absolute_launch_agents_required")
    manifest_payload = read_private_file(manifest_path, PRIVATE_FILE_MODE)
    try:
        manifest = FutureSessionPreparationManifest.model_validate_json(manifest_payload)
    except (TypeError, ValidationError, ValueError):
        raise FutureSessionActivationError("invalid_manifest") from None
    if canonical_manifest_json(manifest).encode() != manifest_payload:
        raise FutureSessionActivationError("invalid_manifest")
    root = manifest_path.parent
    if not manifest_path.is_absolute() or not root.is_absolute():
        raise FutureSessionActivationError("invalid_manifest_path")
    for directory in (root, root / "jobs", root / "receipts", root / "logs"):
        verify_private_directory(directory)
    try:
        authority_commit = current_main_commit(manifest.authority_repository)
    except CurrentMainAuthorityError:
        raise FutureSessionActivationError("current_main_authority_invalid") from None
    if authority_commit != manifest.scheduler_main_sha:
        raise FutureSessionActivationError("current_main_authority_invalid")
    verify_frozen_runtime(manifest.frozen_runtime, manifest.runtime_commit_sha)
    entries = tuple(verify_entry(entry, root=root, launch_agents_dir=launch_agents_dir) for entry in manifest.entries)
    if tuple(entry.role for entry in entries) != tuple(FutureSessionUsRole):
        raise FutureSessionActivationError("invalid_manifest_entries")
    return VerifiedActivation(
        entries=entries,
        manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        receipt_path=root / "activation-receipt.json",
    )


def prepare_launch_agents_directory(path: Path) -> None:
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise FutureSessionActivationError("invalid_launch_agents_directory")


def read_private_file(path: Path, expected_mode: int) -> bytes:
    if not path.is_absolute():
        raise FutureSessionActivationError("absolute_input_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_nlink != 1
        ):
            raise FutureSessionActivationError("invalid_private_artifact")
        payload = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ):
            raise FutureSessionActivationError("input_changed")
        return bytes(payload)
    finally:
        os.close(descriptor)


def verify_private_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != PRIVATE_DIRECTORY_MODE
        or metadata.st_nlink < 2
    ):
        raise FutureSessionActivationError("invalid_private_artifact")


def verify_frozen_runtime(runtime: Path, expected_commit: str) -> None:
    if not runtime.is_dir() or not (runtime / ".git").exists():
        raise FutureSessionActivationError("frozen_runtime_invalid")
    try:
        head = git(runtime, "rev-parse", "HEAD")
        tracked = git(runtime, "status", "--porcelain=v1", "--untracked-files=all")
    except FutureSessionActivationError:
        raise FutureSessionActivationError("frozen_runtime_invalid") from None
    if head != expected_commit or tracked:
        raise FutureSessionActivationError("frozen_runtime_invalid")


def verify_entry(entry: PreparedUsRoleArtifact, *, root: Path, launch_agents_dir: Path) -> ActivatedUsRoleArtifact:
    role = entry.role.value
    expected_paths = (
        root / "jobs" / f"{role}.payload.zsh",
        root / "jobs" / f"{role}.persistent.zsh",
        root / "jobs" / f"{role}.plist",
        root / "receipts" / f"{role}.json",
        root / "logs" / f"{role}.stdout.log",
        root / "logs" / f"{role}.stderr.log",
    )
    if expected_paths != (
        entry.payload_wrapper,
        entry.persistent_wrapper,
        entry.persistent_plist,
        entry.receipt,
        entry.stdout_log,
        entry.stderr_log,
    ):
        raise FutureSessionActivationError("noncanonical_artifact_path")
    payload = read_private_file(entry.payload_wrapper, PRIVATE_EXECUTABLE_MODE)
    wrapper = read_private_file(entry.persistent_wrapper, PRIVATE_EXECUTABLE_MODE)
    plist = read_private_file(entry.persistent_plist, PRIVATE_FILE_MODE)
    if (
        hashlib.sha256(payload).hexdigest() != entry.payload_sha256
        or hashlib.sha256(wrapper).hexdigest() != entry.persistent_wrapper_sha256
        or hashlib.sha256(plist).hexdigest() != entry.persistent_plist_sha256
    ):
        raise FutureSessionActivationError("artifact_hash_mismatch")
    installed_plist = launch_agents_dir / f"{entry.label}.plist"
    expected_binding = f"readonly persistent_plist={shlex.quote(str(installed_plist))}\n".encode()
    if expected_binding not in wrapper:
        raise FutureSessionActivationError("installed_plist_binding_invalid")
    return ActivatedUsRoleArtifact(
        role=entry.role,
        label=entry.label,
        source_plist=entry.persistent_plist,
        installed_plist=installed_plist,
    )


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("/usr/bin/git", "-C", str(repository), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise FutureSessionActivationError("frozen_runtime_invalid")
    return completed.stdout.strip()


__all__ = (
    "PRIVATE_DIRECTORY_MODE",
    "PRIVATE_FILE_MODE",
    "VerifiedActivation",
    "prepare_launch_agents_directory",
    "read_private_file",
    "verify_us_future_session_activation",
)
