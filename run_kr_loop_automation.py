#!/usr/bin/env -S uv run --offline --script
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from trading_agent.kr_loop_active_release import (
    InvalidKrLoopActiveReleaseError,
    ensure_bootstrap_active_release,
    load_active_release,
)
from trading_agent.kr_loop_automation_config import (
    InvalidKrLoopAutomationConfigError,
    load_kr_loop_automation_config,
)
from trading_agent.kr_loop_automation_service import (
    InvalidKrLoopAutomationServiceError,
    run_automation_tick,
)
from trading_agent.kr_loop_engineer_store import InvalidKrLoopEngineerStoreError, KrLoopEngineerStore
from trading_agent.kr_loop_launchd import (
    InvalidKrLoopLaunchAgentError,
    install_kr_loop_launch_agents,
    provision_kr_loop_launch_agents,
    verify_kr_loop_launch_agents,
)
from trading_agent.kr_loop_launchd_install import InvalidKrLoopLaunchAgentInstallError
from trading_agent.repository_current_main import CurrentMainAuthorityError, current_main_commit


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    try:
        config = load_kr_loop_automation_config(args.config)
        if args.command == "provision":
            commit = current_main_commit(config.repository)
            _ = ensure_bootstrap_active_release(
                config.active_release,
                config.repository,
                commit,
                dt.datetime.now(dt.UTC),
            )
            paths = provision_kr_loop_launch_agents(config, args.config.expanduser().absolute())
            verification = verify_kr_loop_launch_agents(args.config)
            print(
                json.dumps(
                    {
                        "loop_plist": str(paths.loop_engineer),
                        "loop_sha256": verification.loop_sha256,
                        "research_plist": str(paths.research_agent),
                        "research_sha256": verification.research_sha256,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "verify":
            verification = verify_kr_loop_launch_agents(args.config)
            print(
                json.dumps(
                    asdict(verification),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "install":
            if not install_kr_loop_launch_agents(args.config, args.current_research_plist):
                return 2
            print('{"installed":true,"paper_only":true,"trading_authority":false}')
        elif args.command == "tick":
            print(
                json.dumps(
                    asdict(run_automation_tick(config, dt.datetime.now(dt.UTC))),
                    default=str,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "status":
            _print_status(config.loop_database, config.active_release)
        else:
            return 2
        return 0
    except (
        CurrentMainAuthorityError,
        InvalidKrLoopActiveReleaseError,
        InvalidKrLoopAutomationConfigError,
        InvalidKrLoopAutomationServiceError,
        InvalidKrLoopEngineerStoreError,
        InvalidKrLoopLaunchAgentError,
        InvalidKrLoopLaunchAgentInstallError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        print("invalid KR Loop automation request", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KR Loop automation for paper-only autonomous research")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("provision", "verify", "status", "tick"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
    install = commands.add_parser("install")
    install.add_argument("--config", type=Path, required=True)
    install.add_argument("--current-research-plist", type=Path, required=True)
    return parser


def _print_status(database: Path, active_path: Path) -> None:
    store = KrLoopEngineerStore(database)
    latest = {item.candidate_id: item for item in store.snapshots()}
    releases = store.releases()
    try:
        active = load_active_release(active_path)
    except InvalidKrLoopActiveReleaseError:
        active = None
    payload = {
        "active_release": None
        if active is None
        else {
            "action": active.action,
            "generation": active.generation,
            "release_id": active.release_id,
        },
        "candidate_count": len(latest),
        "paper_only": True,
        "release": None
        if not releases
        else {
            "action": releases[-1].action.value,
            "generation": releases[-1].generation,
            "release_id": releases[-1].release_id,
        },
        "trading_authority": False,
    }
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
