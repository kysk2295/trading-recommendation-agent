#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["anyio>=4.0", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Sequence

import anyio
from pydantic import ValidationError

from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.private_stable_report import InvalidPrivateStableReportError
from trading_agent.repository_current_main import CurrentMainAuthorityError, current_main_commit
from trading_agent.research_agent_cycle_store_support import (
    InvalidResearchAgentCycleStoreError,
    ResearchAgentCycleWriterLeaseUnavailableError,
)
from trading_agent.research_agent_decision import InvalidResearchAgentDecisionError
from trading_agent.research_agent_runtime_lease import ResearchAgentRuntimeLeaseUnavailableError
from trading_agent.research_agent_service_cli_args import (
    config_from_provision_args,
    parse_service_args,
)
from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    InvalidResearchAgentServiceConfigError,
    ResearchAgentServiceConfig,
    canonical_research_agent_service_config_sha256,
    load_research_agent_service_config,
    verify_research_agent_launch_agent,
    write_research_agent_launch_agent,
    write_research_agent_service_config,
)
from trading_agent.research_agent_service_health import (
    HealthEvaluator,
    InvalidResearchAgentServiceHealthError,
    ResearchAgentServiceHealthEvaluation,
    await_fresh_research_agent_service_health,
    evaluate_persisted_research_agent_service_health,
)
from trading_agent.research_agent_service_legacy_current import (
    verify_research_agent_replace_current,
)
from trading_agent.research_agent_service_runtime import (
    InvalidResearchAgentServiceRuntimeError,
    run_service_cycle,
    run_service_forever,
    run_service_tick,
    service_status,
)
from trading_agent.research_agent_systematic import InvalidSystematicResearchActionError
from trading_agent.research_agent_systematic_input_store import (
    InvalidSystematicInputActivationError,
)

Clock = Callable[[], dt.datetime]
CommandRunner = Callable[[tuple[str, ...]], int]
_NOT_LOADED_RETURN_CODE = 113


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    runner: CommandRunner | None = None,
    health_evaluator: HealthEvaluator | None = None,
) -> int:
    try:
        args = parse_service_args(argv)
    except SystemExit as error:
        if error.code is None:
            return 0
        return error.code if isinstance(error.code, int) else 2
    try:
        if args.command == "provision":
            config = config_from_provision_args(args)
            config_path = args.config.expanduser().absolute()
            plist_path = args.plist.expanduser().absolute()
            _ = write_research_agent_service_config(config_path, config)
            _ = write_research_agent_launch_agent(plist_path, config, config_path)
            verification = verify_research_agent_launch_agent(config_path, plist_path)
            print(_verification_json(verification.config_sha256, verification.plist_sha256))
            return 0
        if args.command == "verify":
            verification = verify_research_agent_launch_agent(args.config, args.plist)
            print(_verification_json(verification.config_sha256, verification.plist_sha256))
            return 0
        if args.command == "tick":
            report = run_service_tick(load_research_agent_service_config(args.config), clock())
            print(report.model_dump_json())
            return 0 if report.status != "failed" else 1
        if args.command == "cycle":
            report = run_service_cycle(load_research_agent_service_config(args.config), clock())
            print(report.model_dump_json())
            return 1 if report.status == "partial" else 0
        if args.command == "run":
            anyio.run(run_service_forever, load_research_agent_service_config(args.config))
            return 0
        if args.command == "status":
            _ = verify_research_agent_launch_agent(args.config, args.plist)
            report = service_status(load_research_agent_service_config(args.config), clock())
            print(report.model_dump_json())
            return 0
        if args.command == "activate":
            return _activate(args, _default_runner if runner is None else runner)
        if args.command == "replace":
            return _replace(
                args,
                _default_runner if runner is None else runner,
                clock,
                _default_health_evaluator if health_evaluator is None else health_evaluator,
            )
        return 2
    except (
        CurrentMainAuthorityError,
        InvalidHermesDeliveryStoreError,
        InvalidPrivateStableReportError,
        InvalidResearchAgentCycleStoreError,
        InvalidResearchAgentDecisionError,
        InvalidResearchAgentServiceConfigError,
        InvalidResearchAgentServiceHealthError,
        InvalidResearchAgentServiceRuntimeError,
        InvalidSystematicResearchActionError,
        InvalidSystematicInputActivationError,
        ResearchAgentCycleWriterLeaseUnavailableError,
        ResearchAgentRuntimeLeaseUnavailableError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        TypeError,
        ValidationError,
    ):
        return 2


def _activate(args: argparse.Namespace, runner: CommandRunner) -> int:
    _ = verify_research_agent_launch_agent(args.config, args.plist)
    config = load_research_agent_service_config(args.config)
    _ = current_main_commit(config.project_root)
    domain = f"gui/{os.getuid()}"
    plist = str(args.plist.expanduser().absolute())
    bootstrap = ("/bin/launchctl", "bootstrap", domain, plist)
    if runner(bootstrap) != 0:
        return 2
    target = f"{domain}/{config.label}"
    if runner(("/bin/launchctl", "kickstart", target)) != 0:
        _ = runner(("/bin/launchctl", "bootout", domain, plist))
        return 2
    return 0


def _replace(
    args: argparse.Namespace,
    runner: CommandRunner,
    clock: Clock,
    health_evaluator: HealthEvaluator,
) -> int:
    current = verify_research_agent_replace_current(args.current_config, args.current_plist)
    _ = verify_research_agent_launch_agent(args.candidate_config, args.candidate_plist)
    candidate = load_research_agent_service_config(args.candidate_config)
    if current.project_root != candidate.project_root or candidate.label != RESEARCH_AGENT_SERVICE_LABEL:
        return 2
    _ = current_main_commit(candidate.project_root)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"
    current_plist = str(args.current_plist.expanduser().absolute())
    candidate_plist = str(args.candidate_plist.expanduser().absolute())
    if (
        runner(("/bin/launchctl", "bootout", domain, current_plist)) != 0
        and runner(("/bin/launchctl", "print", target)) != _NOT_LOADED_RETURN_CODE
    ):
        return 2
    started_at = clock()
    if runner(("/bin/launchctl", "bootstrap", domain, candidate_plist)) != 0:
        return _rollback_replacement(
            runner,
            domain,
            target,
            current_plist,
            candidate_plist,
            "candidate_bootstrap_failed",
        )
    if runner(("/bin/launchctl", "kickstart", target)) != 0:
        return _rollback_replacement(
            runner,
            domain,
            target,
            current_plist,
            candidate_plist,
            "candidate_kickstart_failed",
        )
    health = await_fresh_research_agent_service_health(candidate, started_at, clock, health_evaluator)
    if not health.accepted:
        return _rollback_replacement(
            runner,
            domain,
            target,
            current_plist,
            candidate_plist,
            f"health_{health.reason}",
        )
    return 0


def _rollback_replacement(
    runner: CommandRunner,
    domain: str,
    target: str,
    current_plist: str,
    candidate_plist: str,
    reason: str,
) -> int:
    _ = runner(("/bin/launchctl", "bootout", domain, candidate_plist))
    print(f"replace_{reason}", file=sys.stderr)
    if runner(("/bin/launchctl", "bootstrap", domain, current_plist)) != 0:
        print("replace_current_restore_bootstrap_failed", file=sys.stderr)
        return 2
    if runner(("/bin/launchctl", "kickstart", target)) != 0:
        print("replace_current_restore_kickstart_failed", file=sys.stderr)
    return 2


def _default_health_evaluator(
    config: ResearchAgentServiceConfig,
    started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> ResearchAgentServiceHealthEvaluation:
    return evaluate_persisted_research_agent_service_health(
        config.output_root,
        canonical_research_agent_service_config_sha256(config),
        started_at,
        evaluated_at,
    )


def _default_runner(command: tuple[str, ...]) -> int:
    return subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def _verification_json(config_sha256: str, plist_sha256: str) -> str:
    return json.dumps(
        {
            "broker_mutation": 0,
            "config_sha256": config_sha256,
            "plist_sha256": plist_sha256,
            "status": "verified",
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
