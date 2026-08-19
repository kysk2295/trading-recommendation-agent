#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "rich>=14.0", "typer>=0.16", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

import run_kr_strategy_research_live_cycle
from trading_agent.kr_strategy_research_service_config import (
    KR_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS,
    KR_STRATEGY_RESEARCH_SERVICE_LABEL,
    InvalidKrStrategyResearchServiceError,
    KrStrategyResearchServiceConfig,
    load_kr_strategy_research_service_config,
    verify_kr_strategy_research_launch_agent,
    write_kr_strategy_research_launch_agent,
    write_kr_strategy_research_service_config,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME = "kr_strategy_research_service_ko.md"
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent KRX read-only six-agent research source")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    for name in (
        "project-root",
        "uv-path",
        "policy",
        "database",
        "experiment-ledger",
        "delivery-database",
        "calendar-store",
        "cycle-root",
        "live-session-root",
        "market-context-root",
        "runtime-output-root",
        "config",
        "plist",
    ):
        provision.add_argument(f"--{name}", type=Path, required=True)
    for name in ("verify",):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--plist", type=Path, required=True)
    tick = commands.add_parser("tick")
    tick.add_argument("--config", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    try:
        args = parse_args(argv)
        if args.command == "provision":
            config_path = _absolute(args.config)
            config = KrStrategyResearchServiceConfig(
                label=KR_STRATEGY_RESEARCH_SERVICE_LABEL,
                project_root=_absolute(args.project_root),
                uv_path=_absolute(args.uv_path),
                policy=_absolute(args.policy),
                database=_absolute(args.database),
                experiment_ledger=_absolute(args.experiment_ledger),
                delivery_database=_absolute(args.delivery_database),
                calendar_store=_absolute(args.calendar_store),
                cycle_root=_absolute(args.cycle_root),
                live_session_root=_absolute(args.live_session_root),
                market_context_root=_absolute(args.market_context_root),
                output_root=_absolute(args.runtime_output_root),
            )
            _ = write_kr_strategy_research_service_config(config_path, config)
            _ = write_kr_strategy_research_launch_agent(_absolute(args.plist), config, config_path)
            verified = verify_kr_strategy_research_launch_agent(config_path, _absolute(args.plist))
            print(json.dumps({"ready": verified.ready, "interval_seconds": verified.interval_seconds}))
            return 0
        if args.command == "verify":
            verified = verify_kr_strategy_research_launch_agent(args.config, args.plist)
            print(json.dumps({"ready": verified.ready, "interval_seconds": verified.interval_seconds}))
            return 0
        if args.command == "tick":
            config = load_kr_strategy_research_service_config(args.config)
            result = run_kr_strategy_research_live_cycle.main(_cycle_args(config), clock=clock)
            _write_report(config, result, clock())
            return result
        return 2
    except SystemExit as error:
        if error.code is None:
            return 0
        return error.code if isinstance(error.code, int) else 2
    except (
        InvalidKrStrategyResearchServiceError,
        InvalidPrivateStableReportError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return 2


def _cycle_args(config: KrStrategyResearchServiceConfig) -> tuple[str, ...]:
    return (
        "--policy",
        str(config.policy),
        "--database",
        str(config.database),
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--delivery-database",
        str(config.delivery_database),
        "--calendar-store",
        str(config.calendar_store),
        "--cycle-root",
        str(config.cycle_root),
        "--live-session-root",
        str(config.live_session_root),
        "--market-context-root",
        str(config.market_context_root),
    )


def _write_report(
    config: KrStrategyResearchServiceConfig,
    result: int,
    observed_at: dt.datetime,
) -> None:
    write_private_stable_report(
        config.output_root / REPORT_NAME,
        "\n".join(
            (
                "# KRX strategy research source service",
                "",
                "> KIS/LS/DART read-only evidence; no account, balance, position, or order authority.",
                "",
                f"- result: {'complete' if result == 0 else 'blocked'}",
                f"- observed at: {observed_at.isoformat()}",
                f"- launch interval seconds: {KR_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS}",
                "- trading mutation: 0",
                "",
            )
        ),
    )


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


if __name__ == "__main__":
    raise SystemExit(main())
