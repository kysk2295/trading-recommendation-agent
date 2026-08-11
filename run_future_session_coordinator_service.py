from __future__ import annotations

import argparse
import datetime as dt
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.future_session_coordinator_inspectors import inspect_request
from trading_agent.future_session_coordinator_service import tick_service
from trading_agent.future_session_coordinator_service_launchd import (
    LABEL,
    ServicePlistError,
    VerifiedServicePlist,
    open_verified_service_plist,
    provision_service_plist,
    require_verified_service_plist_identity,
    verify_service_plist,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    FutureSessionCoordinatorServiceReport,
    canonical_service_report_json,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    ensure_frozen_runtime,
    load_service_config,
)
from trading_agent.future_session_us_activation_verifier import read_private_file

type CommandRunner = Callable[[tuple[str, ...], tuple[int, ...]], int]


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner | None = None,
) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    config_path = Path(arguments.config).absolute()
    try:
        config = load_service_config(config_path)
        match arguments.command:
            case "provision":
                _verify_authority(config)
                path = provision_service_plist(config, config_path)
                sys.stdout.write(f'{{"plist":"{path}","result":"provisioned"}}\n')
                return 0
            case "verify":
                _verify_authority(config)
                path = verify_service_plist(config, config_path)
                sys.stdout.write(f'{{"plist":"{path}","result":"verified"}}\n')
                return 0
            case "activate":
                _verify_authority(config)
                with open_verified_service_plist(config, config_path) as verified:
                    return _activate(
                        verified,
                        _default_runner if runner is None else runner,
                    )
            case "tick":
                return _tick(config)
            case "run":
                return _run(config)
            case "status":
                return _status(config.state_root / "future-session-coordinator-status.json")
            case unreachable:
                assert_never(unreachable)
    except (
        FrozenRuntimeError,
        ServicePlistError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        sys.stderr.write(f'{{"reason":"{type(error).__name__}","result":"blocked"}}\n')
        return 2


def _verify_authority(config: FutureSessionCoordinatorServiceConfig) -> None:
    _ = inspect_request(config.us_template_request_path)
    _ = inspect_request(config.kr_template_request_path)
    runtime = ensure_frozen_runtime(
        config.authority_repository,
        config.state_root / "frozen-runtimes",
        config.scheduler_main_sha,
    )
    entrypoint = runtime / "run_future_session_coordinator_service.py"
    metadata = entrypoint.lstat()
    if (
        entrypoint.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
    ):
        raise FrozenRuntimeError("frozen_runtime_entrypoint_invalid")


def _activate(verified: VerifiedServicePlist, runner: CommandRunner) -> int:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    inherited = (verified.descriptor,)
    bootstrap = (
        "/bin/launchctl",
        "bootstrap",
        domain,
        f"/dev/fd/{verified.descriptor}",
    )
    _ = os.lseek(verified.descriptor, 0, os.SEEK_SET)
    if runner(bootstrap, inherited) != 0:
        return 2
    try:
        require_verified_service_plist_identity(verified)
    except ServicePlistError:
        _ = runner(("/bin/launchctl", "bootout", target), ())
        return 2
    if runner(("/bin/launchctl", "kickstart", target), ()) != 0:
        _ = runner(("/bin/launchctl", "bootout", target), ())
        return 2
    if runner(("/bin/launchctl", "print", target), ()) != 0:
        _ = runner(("/bin/launchctl", "bootout", target), ())
        return 2
    return 0


def _default_runner(
    command: tuple[str, ...],
    inherited_descriptors: tuple[int, ...],
) -> int:
    return subprocess.run(
        command,
        check=False,
        pass_fds=inherited_descriptors,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def _tick(config: FutureSessionCoordinatorServiceConfig) -> int:
    report = tick_service(config, dt.datetime.now(dt.UTC))
    sys.stdout.write(canonical_service_report_json(report))
    return 0


def _run(config: FutureSessionCoordinatorServiceConfig) -> int:
    while True:
        _ = _tick(config)
        time.sleep(config.poll_interval_seconds)


def _status(path: Path) -> int:
    payload = read_private_file(path, 0o600)
    report = FutureSessionCoordinatorServiceReport.model_validate_json(payload)
    canonical = canonical_service_report_json(report)
    if canonical.encode() != payload:
        raise FrozenRuntimeError("invalid_status_report")
    sys.stdout.write(canonical)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Coordinate provenance-bound US and KR future sessions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("provision", "verify", "activate", "tick", "run", "status"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
