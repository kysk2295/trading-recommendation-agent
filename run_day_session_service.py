#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from trading_agent.day_session_service import run_day_session_service_tick
from trading_agent.day_session_service_config import (
    InvalidDaySessionServiceError,
    KrDaySessionServiceConfig,
    UsDaySessionServiceConfig,
    load_day_session_service_config,
    replace_day_session_launch_agent,
    verify_day_session_launch_agent,
    write_day_session_launch_agent,
    write_day_session_service_config,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent research-only dual-market Day session service")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    provision.add_argument("--market", choices=("us", "kr"), required=True)
    for name in ("project-root", "uv-path", "source-root", "state-root", "config", "plist"):
        provision.add_argument(f"--{name}", type=Path, required=True)
    provision.add_argument("--calendar-store", type=Path)
    provision.add_argument("--experiment-ledger", type=Path)
    provision.add_argument("--expected-commit", required=True)
    for name in ("verify",):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--plist", type=Path, required=True)
    tick = commands.add_parser("tick")
    tick.add_argument("--config", type=Path, required=True)
    replace = commands.add_parser("replace")
    for generation in ("current", "candidate"):
        replace.add_argument(f"--{generation}-config", type=Path, required=True)
        replace.add_argument(f"--{generation}-plist", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "provision":
            config = _config(args)
            wrote_config = write_day_session_service_config(args.config, config)
            wrote_plist = write_day_session_launch_agent(args.plist, config, args.config)
            print(json.dumps({"config_created": wrote_config, "mutation": 0, "plist_created": wrote_plist}))
            return 0
        if args.command == "verify":
            verified = verify_day_session_launch_agent(args.config, args.plist)
            print(json.dumps({"interval_seconds": verified.interval_seconds, "mutation": 0, "ready": True}))
            return 0
        if args.command == "replace":
            replaced = replace_day_session_launch_agent(
                args.current_config,
                args.current_plist,
                args.candidate_config,
                args.candidate_plist,
            )
            print(json.dumps({"cutover": "complete" if replaced else "rolled_back", "mutation": 0}))
            return 0 if replaced else 2
        config = load_day_session_service_config(args.config)
        result = run_day_session_service_tick(config)
        print(json.dumps(asdict(result), separators=(",", ":"), sort_keys=True))
        return 0
    except (InvalidDaySessionServiceError, OSError, TypeError, ValidationError, ValueError):
        print(json.dumps({"mutation": 0, "reason": "service_input_invalid", "status": "blocked"}))
        return 2


def _config(args: argparse.Namespace) -> UsDaySessionServiceConfig | KrDaySessionServiceConfig:
    values = {
        "project_root": args.project_root.expanduser().absolute(),
        "expected_commit": args.expected_commit,
        "uv_path": args.uv_path.expanduser().absolute(),
        "source_root": args.source_root.expanduser().absolute(),
        "state_root": args.state_root.expanduser().absolute(),
    }
    if args.market == "us":
        return UsDaySessionServiceConfig.model_validate(values)
    if args.calendar_store is None or args.experiment_ledger is None:
        raise ValueError
    values |= {
        "calendar_store": args.calendar_store.expanduser().absolute(),
        "experiment_ledger": args.experiment_ledger.expanduser().absolute(),
    }
    return KrDaySessionServiceConfig.model_validate(values)


if __name__ == "__main__":
    raise SystemExit(main())
