#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.us_news_catalyst_day_service import (
    UsNewsCatalystDayServiceLeaseUnavailableError,
    UsNewsCatalystDayServiceRuntime,
    run_us_news_catalyst_day_service_tick,
)
from trading_agent.us_news_catalyst_day_service_config import (
    InvalidUsNewsCatalystDayServiceError,
    UsNewsCatalystDayServiceConfig,
    load_us_news_catalyst_day_service_config,
    verify_us_news_catalyst_launch_agent,
    write_us_news_catalyst_day_service_config,
    write_us_news_catalyst_launch_agent,
)

REPORT_NAME = "us_news_catalyst_day_service_ko.md"
Clock = Callable[[], dt.datetime]
CommandRunner = Callable[[tuple[str, ...]], int]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US news-catalyst read-only shadow day service deployment",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision", help="private config와 LaunchAgent plist 생성")
    provision.add_argument("--label", required=True)
    provision.add_argument("--project-root", type=Path, required=True)
    provision.add_argument("--uv-path", type=Path, required=True)
    provision.add_argument("--registration-manifest", type=Path, required=True)
    provision.add_argument("--experiment-ledger", type=Path, required=True)
    provision.add_argument("--projection-root", type=Path, required=True)
    provision.add_argument("--evidence-root", type=Path, required=True)
    provision.add_argument("--security-master-store", type=Path, required=True)
    provision.add_argument("--session-root", type=Path, required=True)
    provision.add_argument("--runtime-output-root", type=Path, required=True)
    provision.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    provision.add_argument("--config", type=Path, required=True)
    provision.add_argument("--plist", type=Path, required=True)
    provision.add_argument("--output-dir", type=Path, required=True)
    tick = commands.add_parser("tick", help="장전 bootstrap 후 현재 session one-shot tick")
    tick.add_argument("--config", type=Path, required=True)
    tick.add_argument("--output-dir", type=Path, required=True)
    verify = commands.add_parser("verify", help="private config와 LaunchAgent 계약 검증")
    verify.add_argument("--config", type=Path, required=True)
    verify.add_argument("--plist", type=Path, required=True)
    verify.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    runner: CommandRunner | None = None,
) -> int:
    args = parse_args(argv)
    try:
        match args.command:
            case "provision":
                _provision(args)
                return 0 if _write_report(args.output_dir, "provision", "ready", "none") else 1
            case "verify":
                result = verify_us_news_catalyst_launch_agent(args.config, args.plist)
                reported = _write_report(
                    args.output_dir,
                    "verify",
                    "verified" if result.ready else "blocked",
                    "none",
                )
                return 0 if result.ready and reported else 1
            case "tick":
                config = load_us_news_catalyst_day_service_config(args.config)
                active_runner = _production_runner(config) if runner is None else runner
                result = run_us_news_catalyst_day_service_tick(
                    config,
                    clock(),
                    UsNewsCatalystDayServiceRuntime(active_runner),
                )
                reported = _write_report(
                    args.output_dir,
                    "tick",
                    result.status.value,
                    "none" if result.reason_code is None else result.reason_code,
                )
                return 1 if result.status.value == "blocked" or not reported else 0
            case unreachable:
                assert_never(unreachable)
    except (
        InvalidUsNewsCatalystDayServiceError,
        UsNewsCatalystDayServiceLeaseUnavailableError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _ = _write_report(args.output_dir, args.command, "blocked", "none")
        return 1


def _provision(args: argparse.Namespace) -> None:
    config_path = _absolute(args.config)
    config = UsNewsCatalystDayServiceConfig(
        label=args.label,
        project_root=_absolute(args.project_root),
        uv_path=_absolute(args.uv_path),
        registration_manifest=_absolute(args.registration_manifest),
        experiment_ledger=_absolute(args.experiment_ledger),
        projection_root=_absolute(args.projection_root),
        evidence_root=_absolute(args.evidence_root),
        security_master_store=_absolute(args.security_master_store),
        session_root=_absolute(args.session_root),
        output_root=_absolute(args.runtime_output_root),
        secret_path=_absolute(args.secret_path),
    )
    _ = write_us_news_catalyst_day_service_config(config_path, config)
    _ = write_us_news_catalyst_launch_agent(_absolute(args.plist), config, config_path)
    _ = verify_us_news_catalyst_launch_agent(config_path, _absolute(args.plist))


def _production_runner(config: UsNewsCatalystDayServiceConfig) -> CommandRunner:
    def run(command: tuple[str, ...]) -> int:
        return subprocess.run(
            command,
            cwd=config.project_root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode

    return run


def _write_report(output_dir: Path, operation: str, result: str, reason: str) -> bool:
    try:
        write_private_stable_report(
            output_dir / REPORT_NAME,
            "\n".join(
                (
                    "# US news-catalyst day service",
                    "",
                    "> shadow research only; market-data GET only; no account or order authority.",
                    "",
                    f"- operation: {operation}",
                    f"- result: {result}",
                    f"- reason: {reason}",
                    "- launch interval seconds: 30",
                    "- credential value in config/plist: 0",
                    "- account read: 0",
                    "- order mutation: 0",
                    "",
                )
            ),
        )
        return True
    except InvalidPrivateStableReportError:
        return False


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


if __name__ == "__main__":
    raise SystemExit(main())
