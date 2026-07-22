from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError

from .delivery_worker import (
    HermesDeliveryServiceLeaseUnavailableError,
    InvalidHermesDeliveryServiceError,
    run_delivery_service,
)
from .telegram_sender import HermesTelegramSender, InvalidHermesTelegramConfigurationError

_LABEL: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{2,127}$")
_MODULE: Final = "integrations.hermes.trading-agent.service"
_MAX_PLIST_BYTES: Final = 1024 * 1024
ServiceRunner = Callable[[Path], None]
JsonValue = str | int | bool


class InvalidHermesDeliveryServiceConfigurationError(ValueError):
    def __str__(self) -> str:
        return "Hermes delivery service configuration is invalid"


@dataclass(frozen=True, slots=True)
class HermesDeliveryServiceDeployment:
    label: str
    project_root: Path
    python: Path
    profile_root: Path
    database: Path
    plist: Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hermes trading delivery foreground service")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the single foreground delivery worker")
    run.add_argument("--database", type=Path, required=True)
    for name in ("provision", "verify"):
        command = commands.add_parser(name, help=f"{name} a secret-free LaunchAgent")
        command.add_argument("--label", required=True)
        command.add_argument("--project-root", type=Path, required=True)
        command.add_argument("--python", type=Path, required=True)
        command.add_argument("--profile-root", type=Path, required=True)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--plist", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, runner: ServiceRunner | None = None) -> int:
    args = parse_args(argv)
    try:
        match args.command:
            case "run":
                database = _private_database(args.database)
                active_runner = _production_runner if runner is None else runner
                active_runner(database)
                _print({"result": "stopped"})
            case "provision":
                deployment = _deployment(args)
                _ = _write_private_plist(deployment.plist, _launch_agent_bytes(deployment))
                _print({"result": "provisioned"})
            case "verify":
                deployment = _deployment(args)
                if _read_private_plist(deployment.plist) != _launch_agent_bytes(deployment):
                    raise InvalidHermesDeliveryServiceConfigurationError
                _print({"result": "verified"})
            case unreachable:
                assert_never(unreachable)
        return 0
    except (
        HermesDeliveryServiceLeaseUnavailableError,
        InvalidHermesDeliveryServiceError,
        InvalidHermesDeliveryServiceConfigurationError,
        InvalidHermesDeliveryStoreError,
        InvalidHermesTelegramConfigurationError,
        OSError,
        TypeError,
        ValueError,
    ):
        _print({"reason": "invalid_service_configuration", "result": "blocked"})
        return 2


def _production_runner(database: Path) -> None:
    run_delivery_service(database, HermesTelegramSender.from_hermes_config())


def _deployment(args: argparse.Namespace) -> HermesDeliveryServiceDeployment:
    project_root = _directory(args.project_root)
    python = _executable(args.python)
    profile_root = _directory(args.profile_root)
    database = _private_database(args.database)
    plist = _target(args.plist)
    if (
        _LABEL.fullmatch(args.label) is None
        or not (project_root / "AGENTS.md").is_file()
        or not (project_root / "integrations/hermes/trading-agent/service.py").is_file()
        or not (project_root / "integrations/hermes/trading-agent/delivery_worker.py").is_file()
    ):
        raise InvalidHermesDeliveryServiceConfigurationError
    return HermesDeliveryServiceDeployment(args.label, project_root, python, profile_root, database, plist)


def _directory(path: Path) -> Path:
    if not path.is_absolute():
        raise InvalidHermesDeliveryServiceConfigurationError
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_dir():
        raise InvalidHermesDeliveryServiceConfigurationError
    return resolved


def _executable(path: Path) -> Path:
    if not path.is_absolute():
        raise InvalidHermesDeliveryServiceConfigurationError
    link_metadata = path.lstat()
    resolved = path.resolve(strict=True)
    target_metadata = resolved.stat()
    if (
        not (stat.S_ISREG(link_metadata.st_mode) or stat.S_ISLNK(link_metadata.st_mode))
        or link_metadata.st_uid != os.getuid()
        or not stat.S_ISREG(target_metadata.st_mode)
        or target_metadata.st_uid != os.getuid()
        or not os.access(resolved, os.X_OK)
    ):
        raise InvalidHermesDeliveryServiceConfigurationError
    return path


def _private_database(path: Path) -> Path:
    if not path.is_absolute():
        raise InvalidHermesDeliveryServiceConfigurationError
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if (
        resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise InvalidHermesDeliveryServiceConfigurationError
    return resolved


def _target(path: Path) -> Path:
    if not path.is_absolute() or not path.name:
        raise InvalidHermesDeliveryServiceConfigurationError
    parent = path.parent.resolve(strict=True)
    if parent != path.parent or not parent.is_dir():
        raise InvalidHermesDeliveryServiceConfigurationError
    return parent / path.name


def _launch_agent_bytes(deployment: HermesDeliveryServiceDeployment) -> bytes:
    arguments = [
        str(deployment.python),
        "-m",
        _MODULE,
        "run",
        "--database",
        str(deployment.database),
    ]
    payload = {
        "EnvironmentVariables": {
            "HOME": str(Path.home().resolve()),
            "HERMES_HOME": str(deployment.profile_root),
            "PATH": f"{deployment.python.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "VIRTUAL_ENV": str(deployment.python.parent.parent),
        },
        "KeepAlive": True,
        "Label": deployment.label,
        "ProcessType": "Background",
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        "Umask": 0o077,
        "WorkingDirectory": str(deployment.project_root),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def _write_private_plist(path: Path, payload: bytes) -> bool:
    try:
        existing = _read_private_plist(path)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing != payload:
            raise InvalidHermesDeliveryServiceConfigurationError
        return False
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(os.dup(descriptor), "wb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except (OSError, TypeError, ValueError):
        with suppress(FileNotFoundError):
            path.unlink()
        raise
    finally:
        os.close(descriptor)


def _read_private_plist(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > _MAX_PLIST_BYTES
        ):
            raise InvalidHermesDeliveryServiceConfigurationError
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read(_MAX_PLIST_BYTES + 1)
    finally:
        os.close(descriptor)


def _print(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
