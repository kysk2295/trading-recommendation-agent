#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx2

import run_us_runtime_fleet_cycle as cycle_cli
from trading_agent.private_report import write_private_report
from trading_agent.us_market_data_fleet_audit import RuntimeFleetAuditError
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorConfig,
    RuntimeMinuteSupervisorError,
    RuntimeSupervisorOperationBlockedError,
    RuntimeSupervisorOperationResult,
    RuntimeSupervisorStatus,
    run_runtime_minute_supervisor,
)
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore

REPORT_NAME = "us_runtime_fleet_supervisor_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US scanner→자동 SIP profile→M4.4 cycle을 정규장 동안 bounded 반복",
    )
    parser.add_argument("--scanner-store", type=Path, required=True)
    parser.add_argument("--auto-profile-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--audit-store", type=Path, required=True)
    parser.add_argument("--policy-state-store", type=Path, required=True)
    parser.add_argument("--supervisor-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-path", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=390)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--capacity", type=int, default=2)
    parser.add_argument("--max-candidate-age-seconds", type=int, default=30)
    parser.add_argument("--minimum-residency-seconds", type=int, default=120)
    parser.add_argument("--eviction-cooldown-seconds", type=int, default=300)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    client_factory: Callable[[], httpx2.Client] = cycle_cli.create_data_client,
) -> int:
    args = parse_args(argv)
    selected_clock = (lambda: dt.datetime.now(dt.UTC)) if clock is None else clock
    fleet_audit = RuntimeFleetAuditStore(args.audit_store)

    def operation(evaluated_at: dt.datetime) -> RuntimeSupervisorOperationResult:
        code = cycle_cli.main(
            _cycle_arguments(args),
            now=evaluated_at,
            client_factory=client_factory,
        )
        if code != 0:
            raise RuntimeSupervisorOperationBlockedError
        try:
            audit = fleet_audit.latest()
        except RuntimeFleetAuditError:
            raise RuntimeSupervisorOperationBlockedError from None
        if audit is None or audit.evaluated_at != evaluated_at:
            raise RuntimeSupervisorOperationBlockedError
        ready = audit.fleet_status == "ready" and audit.gate_status == "ready"
        return RuntimeSupervisorOperationResult(audit.cycle_id, ready)

    try:
        records = run_runtime_minute_supervisor(
            operation,
            RuntimeMinuteSupervisorConfig(args.cycles, args.interval_seconds),
            clock=selected_clock,
            sleeper=sleeper,
            writer=RuntimeMinuteSupervisorStore(args.supervisor_store),
        )
    except (OSError, RuntimeMinuteSupervisorError, TypeError, ValueError):
        _report(args.output_dir, ("result: blocked", "account/order mutation: 0"))
        return 1
    ready_count = sum(item.status is RuntimeSupervisorStatus.READY for item in records)
    all_ready = bool(records) and ready_count == len(records)
    _report(
        args.output_dir,
        (
            f"result: {'ready' if all_ready else 'blocked'}",
            f"attempt count: {len(records)}",
            f"ready count: {ready_count}",
            f"blocked count: {len(records) - ready_count}",
            "account/order mutation: 0",
        ),
    )
    return 0 if all_ready else 1


def _cycle_arguments(args: argparse.Namespace) -> list[str]:
    return [
        "--scanner-store",
        str(args.scanner_store),
        "--auto-profile-root",
        str(args.auto_profile_root),
        "--runtime-root",
        str(args.runtime_root),
        "--canonical-root",
        str(args.canonical_root),
        "--audit-store",
        str(args.audit_store),
        "--policy-state-store",
        str(args.policy_state_store),
        "--output-dir",
        str(args.output_dir),
        "--secret-path",
        str(args.secret_path),
        "--capacity",
        str(args.capacity),
        "--max-candidate-age-seconds",
        str(args.max_candidate_age_seconds),
        "--minimum-residency-seconds",
        str(args.minimum_residency_seconds),
        "--eviction-cooldown-seconds",
        str(args.eviction_cooldown_seconds),
    ]


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# US runtime fleet supervisor",
            "",
            "> Alpaca SIP GET-only bounded supervisor 결과입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
