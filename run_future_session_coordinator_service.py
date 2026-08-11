from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_coordinator_inspectors import inspect_request
from trading_agent.future_session_coordinator_service import tick_service
from trading_agent.future_session_coordinator_service_launchd import (
    ServicePlistError,
    provision_service_plist,
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


def main(argv: Sequence[str] | None = None) -> int:
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
            case "tick":
                return _tick(config)
            case "run":
                return _run(config)
            case "status":
                return _status(config.state_root / "future-session-coordinator-status.json")
            case unreachable:
                parser.error(f"unknown command: {unreachable}")
    except (
        FrozenRuntimeError,
        ServicePlistError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        sys.stderr.write(f'{{"reason":"{type(error).__name__}","result":"blocked"}}\n')
        return 2


def _verify_authority(config: FutureSessionCoordinatorServiceConfig) -> None:
    _ = inspect_request(config.us_template_request_path)
    _ = inspect_request(config.kr_template_request_path)
    _ = ensure_frozen_runtime(
        config.authority_repository,
        config.state_root / "frozen-runtimes",
    )


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
        raise ValueError("invalid status report")
    sys.stdout.write(canonical)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Coordinate provenance-bound US and KR future sessions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("provision", "verify", "tick", "run", "status"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
