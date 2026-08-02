from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

SANDBOX_EXECUTABLE: Final = Path("/usr/bin/sandbox-exec")
SANDBOX_PROFILE_VERSION: Final = "generated_strategy_sandbox_v1"
_MAX_EXECUTABLE_BYTES: Final = 512 * 1024 * 1024
_MAX_INVENTORY_BYTES: Final = 4 * 1024 * 1024
_INVENTORY_SCRIPT: Final = (
    "from importlib.metadata import distributions;"
    "rows={f\"{name.casefold()}=={item.version}\" for item in distributions() "
    "if (name:=item.metadata.get('Name'))};"
    "print('\\n'.join(sorted(rows)))"
)


class GeneratedStrategyRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"generated strategy runtime blocked: {self.reason}"


class GeneratedStrategyRuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    python_executable: Path
    python_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_executable: Path
    sandbox_profile_version: Literal["generated_strategy_sandbox_v1"]

    @model_validator(mode="after")
    def validate_fingerprint(self) -> Self:
        seed = _RuntimeFingerprintSeed(
            self.python_executable,
            self.python_executable_sha256,
            self.package_inventory_sha256,
            self.sandbox_executable,
            self.sandbox_profile_version,
        )
        if self.runtime_fingerprint != _runtime_fingerprint(seed):
            raise GeneratedStrategyRuntimeError("runtime_fingerprint_invalid")
        return self


@dataclass(frozen=True, slots=True)
class _RuntimeFingerprintSeed:
    python_executable: Path
    python_executable_sha256: str
    package_inventory_sha256: str
    sandbox_executable: Path
    sandbox_profile_version: str


def resolve_generated_strategy_runtime(executable: Path) -> GeneratedStrategyRuntimeIdentity:
    python_executable = _require_executable(executable, owners=frozenset((0, os.getuid())))
    sandbox_executable = _require_executable(SANDBOX_EXECUTABLE, owners=frozenset((0,)))
    executable_sha256 = _file_sha256(python_executable)
    inventory_sha256 = hashlib.sha256(_package_inventory(python_executable)).hexdigest()
    seed = _RuntimeFingerprintSeed(
        python_executable,
        executable_sha256,
        inventory_sha256,
        sandbox_executable,
        SANDBOX_PROFILE_VERSION,
    )
    fingerprint = _runtime_fingerprint(seed)
    return GeneratedStrategyRuntimeIdentity(
        python_executable=python_executable,
        python_executable_sha256=executable_sha256,
        package_inventory_sha256=inventory_sha256,
        runtime_fingerprint=fingerprint,
        sandbox_executable=sandbox_executable,
        sandbox_profile_version=SANDBOX_PROFILE_VERSION,
    )


def require_generated_strategy_runtime(
    expected: GeneratedStrategyRuntimeIdentity,
) -> GeneratedStrategyRuntimeIdentity:
    executable = _require_executable(expected.python_executable, owners=frozenset((0, os.getuid())))
    if _file_sha256(executable) != expected.python_executable_sha256:
        raise GeneratedStrategyRuntimeError("python_executable_changed")
    current = resolve_generated_strategy_runtime(executable)
    if current != expected:
        raise GeneratedStrategyRuntimeError("runtime_identity_changed")
    return current


def _require_executable(path: Path, *, owners: frozenset[int]) -> Path:
    try:
        resolved = path.absolute().resolve(strict=True)
        _reject_symlink_components(resolved)
        metadata = resolved.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in owners
            or metadata.st_nlink != 1
            or not os.access(resolved, os.X_OK)
        ):
            raise GeneratedStrategyRuntimeError("executable_identity_invalid")
        return resolved
    except OSError as error:
        raise GeneratedStrategyRuntimeError("executable_unavailable") from error


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise GeneratedStrategyRuntimeError("executable_symlink_forbidden")


def _file_sha256(path: Path) -> str:
    try:
        metadata = path.stat()
        if metadata.st_size <= 0 or metadata.st_size > _MAX_EXECUTABLE_BYTES:
            raise GeneratedStrategyRuntimeError("executable_size_invalid")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = path.stat()
        if (
            (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise GeneratedStrategyRuntimeError("executable_changed_during_hash")
        return digest.hexdigest()
    except OSError as error:
        raise GeneratedStrategyRuntimeError("executable_hash_failed") from error


def _package_inventory(executable: Path) -> bytes:
    try:
        completed = subprocess.run(
            (str(executable), "-I", "-c", _INVENTORY_SCRIPT),
            check=False,
            capture_output=True,
            timeout=30.0,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": ""},
        )
        if completed.returncode != 0 or len(completed.stdout) > _MAX_INVENTORY_BYTES:
            raise GeneratedStrategyRuntimeError("package_inventory_failed")
        return completed.stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise GeneratedStrategyRuntimeError("package_inventory_failed") from error


def _runtime_fingerprint(seed: _RuntimeFingerprintSeed) -> str:
    payload = json.dumps(
        {
            "package_inventory_sha256": seed.package_inventory_sha256,
            "python_executable": str(seed.python_executable),
            "python_executable_sha256": seed.python_executable_sha256,
            "sandbox_executable": str(seed.sandbox_executable),
            "sandbox_profile_version": seed.sandbox_profile_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = (
    "SANDBOX_EXECUTABLE",
    "SANDBOX_PROFILE_VERSION",
    "GeneratedStrategyRuntimeError",
    "GeneratedStrategyRuntimeIdentity",
    "require_generated_strategy_runtime",
    "resolve_generated_strategy_runtime",
)
