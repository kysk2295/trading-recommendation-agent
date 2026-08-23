#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

import run_us_strategy_research_live_cycle
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.us_strategy_research_service_config import (
    US_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS,
    US_STRATEGY_RESEARCH_SERVICE_LABEL,
    InvalidUsStrategyResearchServiceError,
    UsStrategyResearchServiceConfig,
    load_us_strategy_research_service_config,
    verify_us_strategy_research_launch_agent,
    write_us_strategy_research_launch_agent,
    write_us_strategy_research_service_config,
)

REPORT_NAME = "us_strategy_research_service_ko.md"
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent US SIP read-only six-agent research source")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    for name in (
        "project-root",
        "uv-path",
        "credentials-path",
        "live-session-root",
        "market-context-root",
        "day-source-root",
        "news-database",
        "runtime-output-root",
        "config",
        "plist",
    ):
        provision.add_argument(f"--{name}", type=Path, required=True)
    provision.add_argument("--expected-commit", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--config", type=Path, required=True)
    verify.add_argument("--plist", type=Path, required=True)
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
            config = UsStrategyResearchServiceConfig(
                label=US_STRATEGY_RESEARCH_SERVICE_LABEL,
                project_root=_absolute(args.project_root),
                expected_commit=args.expected_commit,
                uv_path=_absolute(args.uv_path),
                credentials_path=_absolute(args.credentials_path),
                live_session_root=_absolute(args.live_session_root),
                market_context_root=_absolute(args.market_context_root),
                day_source_root=_absolute(args.day_source_root),
                news_database=_absolute(args.news_database),
                output_root=_absolute(args.runtime_output_root),
            )
            _ = write_us_strategy_research_service_config(config_path, config)
            _ = write_us_strategy_research_launch_agent(_absolute(args.plist), config, config_path)
            verified = verify_us_strategy_research_launch_agent(config_path, _absolute(args.plist))
            print(json.dumps({"ready": verified.ready, "interval_seconds": verified.interval_seconds}))
            return 0
        if args.command == "verify":
            verified = verify_us_strategy_research_launch_agent(args.config, args.plist)
            print(json.dumps({"ready": verified.ready, "interval_seconds": verified.interval_seconds}))
            return 0
        if args.command == "tick":
            config = load_us_strategy_research_service_config(args.config)
            if _authority_reason(config) is not None:
                _write_report(config, 2, clock())
                return 2
            result = run_us_strategy_research_live_cycle.main(_cycle_args(config), clock=clock)
            _write_report(config, result, clock())
            return result
        return 2
    except SystemExit as error:
        if error.code is None:
            return 0
        return error.code if isinstance(error.code, int) else 2
    except (
        InvalidPrivateStableReportError,
        InvalidUsStrategyResearchServiceError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return 2


def _cycle_args(config: UsStrategyResearchServiceConfig) -> tuple[str, ...]:
    return (
        "--credentials-path",
        str(config.credentials_path),
        "--live-session-root",
        str(config.live_session_root),
        "--market-context-root",
        str(config.market_context_root),
        "--day-source-root",
        str(config.day_source_root),
        "--news-database",
        str(config.news_database),
    )


def _write_report(
    config: UsStrategyResearchServiceConfig,
    result: int,
    observed_at: dt.datetime,
) -> None:
    write_private_stable_report(
        config.output_root / REPORT_NAME,
        "\n".join(
            (
                "# US strategy research source service",
                "",
                "> Alpaca SIP GET-only evidence; no account, position, or order authority.",
                "",
                f"- result: {'complete' if result == 0 else 'blocked'}",
                f"- observed at: {observed_at.isoformat()}",
                f"- launch interval seconds: {US_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS}",
                "- trading mutation: 0",
                "",
            )
        ),
    )


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


def _authority_reason(config: UsStrategyResearchServiceConfig) -> str | None:
    try:
        root = config.project_root.resolve(strict=True)
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = _git(root, "rev-parse", "HEAD")
        local_main = _git(root, "rev-parse", "refs/heads/main")
        tracked = _git(root, "status", "--porcelain=v1", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "project_root_invalid"
    if root != config.project_root or branch != "main" or head != local_main or head != config.expected_commit:
        return "commit_mismatch"
    return "tracked_worktree_dirty" if tracked else None


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
