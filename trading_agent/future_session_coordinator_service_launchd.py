from __future__ import annotations

import os
import plistlib
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
    require_same_file,
)

LABEL: Final = "ai.trading-agent.future-session-coordinator"


@dataclass(frozen=True, slots=True)
class ServicePlistError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class VerifiedServicePlist:
    path: Path
    parent_descriptor: int
    descriptor: int
    expected: bytes


def service_plist_path(config: FutureSessionCoordinatorServiceConfig) -> Path:
    return config.launch_agents_dir / f"{LABEL}.plist"


def canonical_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> bytes:
    logs = config.state_root / "logs"
    runtime = config.state_root / "frozen-runtimes" / config.scheduler_main_sha
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(runtime / "run_future_session_coordinator_service.py"),
            "run",
            "--config",
            str(config_path),
        ],
        "KeepAlive": True,
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "coordinator.stdout.log"),
        "StandardErrorPath": str(logs / "coordinator.stderr.log"),
        "WorkingDirectory": str(runtime),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def provision_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> Path:
    destination = service_plist_path(config)
    expected = canonical_service_plist(config, config_path)
    (config.state_root / "logs").mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config.state_root / "logs", 0o700)
    try:
        parent = open_private_parent(config.launch_agents_dir, create=True)
        try:
            require_private_directory_query_only(parent)
            require_open_directory_path(config.launch_agents_dir, parent)
            try:
                descriptor = os.open(
                    destination.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                try:
                    _require_private_file(descriptor)
                    _write_all(descriptor, expected)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(parent)
            require_open_directory_path(config.launch_agents_dir, parent)
        finally:
            os.close(parent)
        return verify_service_plist(config, config_path)
    except ServicePlistError:
        raise
    except (OSError, TypeError, ValueError):
        raise ServicePlistError("service_plist_invalid") from None


def verify_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> Path:
    with open_verified_service_plist(config, config_path) as verified:
        return verified.path


@contextmanager
def open_verified_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> Iterator[VerifiedServicePlist]:
    destination = service_plist_path(config)
    parent: int | None = None
    descriptor: int | None = None
    try:
        parent = open_private_parent(config.launch_agents_dir, create=False)
        require_private_directory_query_only(parent)
        descriptor = os.open(
            destination.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        _require_private_file(descriptor)
        expected = canonical_service_plist(config, config_path)
        if _read_exact(descriptor, len(expected)) != expected:
            raise ServicePlistError("service_plist_invalid")
        _require_named_identity(parent, destination.name, descriptor)
        require_open_directory_path(config.launch_agents_dir, parent)
        yield VerifiedServicePlist(destination, parent, descriptor, expected)
    except FileNotFoundError:
        raise ServicePlistError("service_plist_missing") from None
    except ServicePlistError:
        raise
    except (OSError, TypeError, ValueError):
        raise ServicePlistError("service_plist_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def require_verified_service_plist_identity(verified: VerifiedServicePlist) -> None:
    try:
        _require_private_file(verified.descriptor)
        if _read_exact(verified.descriptor, len(verified.expected)) != verified.expected:
            raise ServicePlistError("service_plist_invalid")
        _require_named_identity(
            verified.parent_descriptor,
            verified.path.name,
            verified.descriptor,
        )
        require_open_directory_path(verified.path.parent, verified.parent_descriptor)
    except ServicePlistError:
        raise
    except (OSError, TypeError, ValueError):
        raise ServicePlistError("service_plist_invalid") from None


def _require_private_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ServicePlistError("service_plist_invalid")


def _require_named_identity(parent: int, name: str, expected: int) -> None:
    current = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=parent,
    )
    try:
        _require_private_file(current)
        require_same_file(expected, current)
    finally:
        os.close(current)


def _read_exact(descriptor: int, expected_size: int) -> bytes:
    before = os.fstat(descriptor)
    _ = os.lseek(descriptor, 0, os.SEEK_SET)
    payload = bytearray()
    while chunk := os.read(descriptor, expected_size + 1 - len(payload)):
        payload.extend(chunk)
        if len(payload) > expected_size:
            break
    after = os.fstat(descriptor)
    if (
        len(payload) != expected_size
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise ServicePlistError("service_plist_invalid")
    return bytes(payload)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise ServicePlistError("service_plist_invalid")
        offset += written


__all__ = (
    "LABEL",
    "ServicePlistError",
    "VerifiedServicePlist",
    "canonical_service_plist",
    "open_verified_service_plist",
    "provision_service_plist",
    "require_verified_service_plist_identity",
    "service_plist_path",
    "verify_service_plist",
)
