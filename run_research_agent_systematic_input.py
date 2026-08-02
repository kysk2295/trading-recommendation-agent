#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import TypedDict, assert_never

from pydantic import ValidationError

from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.research_agent_systematic_input_evidence import (
    SystematicInputEvidenceError,
    verify_systematic_input_evidence_graph,
)
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
    SystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    InvalidSystematicInputActivationError,
    load_systematic_input_activation,
    write_systematic_input_activation,
)


class _Command(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    STATUS = "status"


class _ReadySummary(TypedDict):
    bar_count: int
    broker_mutation: int
    foundation_sha256: str
    input_sha256: str
    selected_session_count: int
    status: str


class _BlockedSummary(TypedDict):
    attempt_report_sha256: str | None
    broker_mutation: int
    reason_code: str
    status: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Activate or inspect one verified bounded Systematic research input graph."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    ready = commands.add_parser("ready", help="Verify exactly one graph and activate it.")
    ready.add_argument("--artifact-root", type=Path, required=True)
    ready.add_argument("--activation", type=Path, required=True)
    blocked = commands.add_parser("blocked", help="Replace the pointer with a report-bound blocked state.")
    blocked.add_argument("--reason-code", required=True)
    blocked.add_argument("--attempt-report", type=Path, required=True)
    blocked.add_argument("--activation", type=Path, required=True)
    status = commands.add_parser("status", help="Revalidate and report the current activation.")
    status.add_argument("--activation", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        command = _Command(args.command)
        match command:
            case _Command.READY:
                activation = _activate_ready(args.artifact_root, args.activation)
            case _Command.BLOCKED:
                activation = _activate_blocked(args.reason_code, args.attempt_report, args.activation)
            case _Command.STATUS:
                activation = load_systematic_input_activation(_absolute_path(args.activation))
            case unreachable:
                assert_never(unreachable)
    except (
        InvalidPrivateImmutableFileError,
        InvalidSystematicInputActivationError,
        OSError,
        SystematicInputEvidenceError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        print('{"broker_mutation":0,"status":"invalid"}', file=sys.stderr)
        return 2
    print(json.dumps(_summary(activation), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


def _activate_ready(artifact_root: Path, activation_path: Path) -> SystematicInputActivation:
    facts = verify_systematic_input_evidence_graph(_absolute_path(artifact_root))
    activation = ReadySystematicInputActivation(
        input_csv_path=facts.input_csv_path,
        input_csv_sha256=facts.input_csv_sha256,
        dataset_receipt_path=facts.dataset_receipt_path,
        dataset_receipt_sha256=facts.dataset_receipt_sha256,
        catalog_receipt_path=facts.catalog_receipt_path,
        catalog_receipt_sha256=facts.catalog_receipt_sha256,
        input_binding_receipt_path=facts.input_binding_receipt_path,
        input_binding_receipt_sha256=facts.input_binding_receipt_sha256,
        foundation_path=facts.foundation_path,
        foundation_sha256=facts.foundation_sha256,
        producer_commit_sha=facts.producer_commit_sha,
        input_sha256=facts.input_sha256,
        selected_session_dates=facts.selected_session_dates,
        bar_count=facts.bar_count,
        max_sessions=facts.max_sessions,
        max_bars=facts.max_bars,
        rss_limit_gib=facts.rss_limit_gib,
        activated_at=facts.registered_at,
    )
    pointer = _absolute_path(activation_path)
    write_systematic_input_activation(pointer, activation)
    return activation


def _activate_blocked(
    reason_code: str,
    attempt_report: Path,
    activation_path: Path,
) -> SystematicInputActivation:
    report = _absolute_path(attempt_report)
    report_payload = read_private_text(report)
    activation = BlockedSystematicInputActivation(
        reason_code=reason_code,
        attempted_at=dt.datetime.now(dt.UTC),
        attempt_report_path=report,
        attempt_report_sha256=hashlib.sha256(report_payload.encode()).hexdigest(),
    )
    pointer = _absolute_path(activation_path)
    write_systematic_input_activation(pointer, activation)
    return activation


def _absolute_path(path: Path) -> Path:
    if not path.is_absolute() or Path(os.path.realpath(path)) != path:
        raise ValueError
    return path


def _summary(activation: SystematicInputActivation) -> _ReadySummary | _BlockedSummary:
    match activation:
        case ReadySystematicInputActivation() as ready:
            return _ReadySummary(
                bar_count=ready.bar_count,
                broker_mutation=0,
                foundation_sha256=ready.foundation_sha256,
                input_sha256=ready.input_sha256,
                selected_session_count=len(ready.selected_session_dates),
                status=ready.status,
            )
        case BlockedSystematicInputActivation() as blocked:
            return _BlockedSummary(
                attempt_report_sha256=blocked.attempt_report_sha256,
                broker_mutation=0,
                reason_code=blocked.reason_code,
                status=blocked.status,
            )
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    raise SystemExit(main())
