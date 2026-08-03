#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from trading_agent.intraday_promotion_controller import (
    IntradayPromotionApprovalRequest,
    IntradayPromotionControlCommand,
    IntradayPromotionRequest,
    approve_intraday_promotion,
    assess_intraday_promotion,
    control_intraday_promotion,
)
from trading_agent.intraday_promotion_evidence import IntradayPromotionEvidencePaths


@dataclass(frozen=True, slots=True)
class _CliOutput:
    result: str
    identifier: str | None
    artifact_created: int
    authority_bindings_created: int
    lifecycle_events_created: int
    blockers: tuple[str, ...]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate and explicitly approve an intraday champion transition")
    commands = parser.add_subparsers(dest="command", required=True)
    assess = commands.add_parser("assess", help="evaluate immutable promotion evidence")
    _add_request_arguments(assess)
    assess.add_argument("--output-dir", type=Path, required=True)
    assess.add_argument("--timestamp", type=_timestamp)
    approve = commands.add_parser("approve", help="persist a distinct manual approval receipt")
    approve.add_argument("--assessment", type=Path, required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--output-dir", type=Path, required=True)
    approve.add_argument("--timestamp", type=_timestamp)
    control = commands.add_parser("control", help="apply one approved next-session champion transition")
    _add_request_arguments(control)
    control.add_argument("--assessment", type=Path, required=True)
    control.add_argument("--approval", type=Path, required=True)
    control.add_argument("--timestamp", type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(dt.UTC) if args.timestamp is None else args.timestamp
    try:
        match args.command:
            case "assess":
                assessment, _, created = assess_intraday_promotion(
                    _request(args),
                    timestamp,
                    args.output_dir,
                )
                _emit(
                    _CliOutput(
                        assessment.content.status.value,
                        assessment.assessment_id,
                        int(created),
                        0,
                        0,
                        assessment.content.blockers,
                    )
                )
                return 2
            case "approve":
                approval, _, created = approve_intraday_promotion(
                    IntradayPromotionApprovalRequest(
                        args.assessment,
                        args.approver,
                        timestamp,
                        args.output_dir,
                    )
                )
                _emit(_CliOutput("approved", approval.approval_id, int(created), 0, 0, ()))
                return 0
            case "control":
                result = control_intraday_promotion(
                    IntradayPromotionControlCommand(
                        _request(args),
                        args.assessment,
                        args.approval,
                        timestamp,
                    )
                )
                _emit(
                    _CliOutput(
                        "transitioned",
                        str(result.event.strategy_version),
                        0,
                        result.authority_bindings_created,
                        result.lifecycle_events_created,
                        (),
                    )
                )
                return 0
            case _:
                return 1
    except (OSError, RuntimeError, TypeError, ValueError):
        _emit(_CliOutput("blocked_source", None, 0, 0, 0, ("invalid_or_untrusted_source",)))
        return 1


def _add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--plateau", type=Path, required=True)
    parser.add_argument("--broker-shadow", type=Path, required=True)
    parser.add_argument("--sip", type=Path, required=True)
    parser.add_argument("--session-date", type=dt.date.fromisoformat, required=True)


def _request(args: argparse.Namespace) -> IntradayPromotionRequest:
    return IntradayPromotionRequest(
        experiment_ledger=args.experiment_ledger,
        evidence=IntradayPromotionEvidencePaths(
            audit=args.audit,
            comparison=args.comparison,
            diagnostics=args.diagnostics,
            plateau=args.plateau,
            broker_shadow=args.broker_shadow,
            sip=args.sip,
        ),
        session_date=args.session_date,
    )


def _emit(output: _CliOutput) -> None:
    print(
        json.dumps(
            {
                "allocation_mutations": 0,
                "artifact_created": output.artifact_created,
                "authority_bindings_created": output.authority_bindings_created,
                "blockers": output.blockers,
                "broker_mutations": 0,
                "identifier": output.identifier,
                "lifecycle_events_created": output.lifecycle_events_created,
                "network_access": 0,
                "order_authority_mutations": 0,
                "result": output.result,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include an offset")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
