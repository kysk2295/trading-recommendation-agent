from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.future_session_coordinator_bootstrap import (
    bootstrap_coordinator_bundle,
    load_bootstrap_manifest,
)
from trading_agent.future_session_coordinator_service import tick_service
from trading_agent.future_session_coordinator_service_health import (
    CoordinatorClock,
    CoordinatorHealthEvaluator,
    CoordinatorSleeper,
    evaluate_current_coordinator_health,
    evaluate_persisted_coordinator_health,
)
from trading_agent.future_session_coordinator_service_launchd import (
    ServicePlistError,
    provision_service_plist,
    verify_service_plist,
)
from trading_agent.future_session_coordinator_service_lifecycle import (
    CoordinatorCommandRunner,
    activate_coordinator_service,
    replace_coordinator_service,
    restart_coordinator_service,
    verify_coordinator_authority,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    canonical_service_report_json,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    load_service_config,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CoordinatorCommandRunner | None = None,
    clock: CoordinatorClock | None = None,
    health_evaluator: CoordinatorHealthEvaluator | None = None,
    sleeper: CoordinatorSleeper = time.sleep,
) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    active_clock = _utc_now if clock is None else clock
    active_health_evaluator = evaluate_persisted_coordinator_health if health_evaluator is None else health_evaluator
    try:
        if arguments.command == "bootstrap":
            manifest = load_bootstrap_manifest(Path(arguments.manifest).absolute())
            config_path = bootstrap_coordinator_bundle(manifest)
            config = load_service_config(config_path)
            verify_coordinator_authority(config)
            plist = provision_service_plist(config, config_path)
            sys.stdout.write(f'{{"config":"{config_path}","plist":"{plist}","result":"bootstrapped"}}\n')
            return 0
        if arguments.command == "replace":
            current_path = Path(arguments.current_config).absolute()
            candidate_path = Path(arguments.candidate_config).absolute()
            current = load_service_config(current_path)
            candidate = load_service_config(candidate_path)
            return replace_coordinator_service(
                current,
                current_path,
                candidate,
                candidate_path,
                _default_runner if runner is None else runner,
                active_clock,
                active_health_evaluator,
                sleeper,
            )
        config_path = Path(arguments.config).absolute()
        config = load_service_config(config_path)
        match arguments.command:
            case "provision":
                verify_coordinator_authority(config)
                path = provision_service_plist(config, config_path)
                sys.stdout.write(f'{{"plist":"{path}","result":"provisioned"}}\n')
                return 0
            case "verify":
                verify_coordinator_authority(config)
                path = verify_service_plist(config, config_path)
                sys.stdout.write(f'{{"plist":"{path}","result":"verified"}}\n')
                return 0
            case "activate":
                return activate_coordinator_service(
                    config,
                    config_path,
                    _default_runner if runner is None else runner,
                    active_clock,
                    active_health_evaluator,
                    sleeper,
                )
            case "restart":
                return restart_coordinator_service(
                    config,
                    config_path,
                    _default_runner if runner is None else runner,
                    active_clock,
                    active_health_evaluator,
                    sleeper,
                )
            case "tick":
                observed_at = active_clock()
                return _tick(config, observed_at, observed_at)
            case "run":
                return _run(config, active_clock)
            case "status":
                return _status(config, active_clock())
            case unreachable:
                assert_never(unreachable)
    except (
        FrozenRuntimeError,
        OverflowError,
        ServicePlistError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        sys.stderr.write(f'{{"reason":"{type(error).__name__}","result":"blocked"}}\n')
        return 2


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


def _tick(
    config: FutureSessionCoordinatorServiceConfig,
    observed_at: dt.datetime,
    service_started_at: dt.datetime,
) -> int:
    report = tick_service(
        config,
        observed_at,
        service_started_at=service_started_at,
    )
    sys.stdout.write(canonical_service_report_json(report))
    return 0


def _run(
    config: FutureSessionCoordinatorServiceConfig,
    clock: CoordinatorClock,
) -> int:
    service_started_at = clock()
    while True:
        _ = _tick(config, clock(), service_started_at)
        time.sleep(config.poll_interval_seconds)


def _status(
    config: FutureSessionCoordinatorServiceConfig,
    evaluated_at: dt.datetime,
) -> int:
    health = evaluate_current_coordinator_health(config, evaluated_at)
    if not health.accepted:
        raise FrozenRuntimeError(f"status_health_{health.reason}")
    if health.report is None:
        raise FrozenRuntimeError("status_health_report_missing")
    sys.stdout.write(canonical_service_report_json(health.report))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Coordinate provenance-bound US and KR future sessions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--manifest", required=True)
    replace = commands.add_parser("replace")
    replace.add_argument("--current-config", required=True)
    replace.add_argument("--candidate-config", required=True)
    for name in (
        "provision",
        "verify",
        "activate",
        "restart",
        "tick",
        "run",
        "status",
    ):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
    return parser


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


if __name__ == "__main__":
    raise SystemExit(main())
