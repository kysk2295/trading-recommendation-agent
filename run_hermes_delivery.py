#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_projection import (
    HermesProjectionSources,
    InvalidHermesProjectionSourceError,
    project_contract_outboxes,
)
from trading_agent.hermes_delivery_redrive import (
    HermesDeliveryRedriveRequest,
    InvalidHermesDeliveryRedriveError,
    redrive_timeout_dead_letter,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.hermes_query_service import HermesAgentQueryService, InvalidHermesQueryError
from trading_agent.hermes_us_session_commands import (
    FinalizeUsSessionCommand,
    ProjectUsSessionCommand,
    ReconcileUsSessionCommand,
    finalize_us_session_command,
    project_us_session_command,
    reconcile_us_session_command,
)
from trading_agent.kr_source_cycle_delivery import (
    InvalidKrSourceCycleDeliveryError,
    KrSourceCycleDeliveryRequest,
    project_kr_source_cycle_incident,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.private_stable_report import InvalidPrivateStableReportError
from trading_agent.us_session_delivery_reconciliation import InvalidUsSessionDeliveryReconciliationError
from trading_agent.us_session_delivery_terminal import InvalidUsSessionDeliveryTerminalError

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project and query Hermes trading-agent deliveries")
    commands = parser.add_subparsers(dest="command", required=True)
    project = commands.add_parser("project", help="project immutable source outboxes")
    project.add_argument("--database", type=Path, required=True)
    project.add_argument("--opportunities", type=Path, required=True)
    project.add_argument("--signals", type=Path, required=True)
    session = commands.add_parser(
        "project-session",
        help="project deduplicated US session watches and signal replies",
    )
    session.add_argument("--database", type=Path, required=True)
    session.add_argument("--opportunities", type=Path, required=True)
    session.add_argument("--signals", type=Path, required=True)
    session.add_argument("--session-date", type=_date, required=True)
    finalize = commands.add_parser(
        "finalize-session",
        help="publish a closed US session no-setup or signal-count result",
    )
    finalize.add_argument("--database", type=Path, required=True)
    finalize.add_argument("--opportunities", type=Path, required=True)
    finalize.add_argument("--signals", type=Path, required=True)
    finalize.add_argument("--session-date", type=_date, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    reconcile = commands.add_parser(
        "reconcile-session",
        help="reconcile exact US session deliveries and acknowledgements",
    )
    reconcile.add_argument("--database", type=Path, required=True)
    reconcile.add_argument("--opportunities", type=Path, required=True)
    reconcile.add_argument("--signals", type=Path, required=True)
    reconcile.add_argument("--session-date", type=_date, required=True)
    reconcile.add_argument("--output", type=Path, required=True)
    reconcile.add_argument("--terminal-artifact", type=Path)
    kr_cycle = commands.add_parser(
        "project-kr-cycle",
        help="project a current incomplete KR source cycle incident",
    )
    kr_cycle.add_argument("--database", type=Path, required=True)
    kr_cycle.add_argument("--source-database", type=Path, required=True)
    kr_cycle.add_argument("--collection-cycle-id", required=True)
    redrive = commands.add_parser("redrive", help="redrive one timeout dead letter as a new root event")
    redrive.add_argument("--database", type=Path, required=True)
    redrive.add_argument("--dead-letter-transition-id", required=True)
    query = commands.add_parser("query", help="query separate agent opinions")
    query.add_argument("--database", type=Path, required=True)
    query.add_argument("--symbol", required=True)
    query.add_argument("--observed-at", type=_datetime, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    try:
        store = HermesDeliveryStore(args.database)
        match args.command:
            case "project":
                sources = HermesProjectionSources(
                    opportunity_outbox=args.opportunities,
                    signal_outbox=args.signals,
                )
                with store.writer() as writer:
                    result = project_contract_outboxes(sources, writer)
                _print({"examined": result.examined, "inserted": result.inserted, "result": "projected"})
            case "project-session":
                result = project_us_session_command(
                    ProjectUsSessionCommand(
                        _sources(args.opportunities, args.signals),
                        args.session_date,
                    ),
                    store,
                )
                _print(
                    {
                        "examined": result.examined,
                        "inserted": result.inserted,
                        "result": "projected_session",
                    }
                )
            case "finalize-session":
                result = finalize_us_session_command(
                    FinalizeUsSessionCommand(
                        _sources(args.opportunities, args.signals),
                        args.session_date,
                        clock(),
                        args.output,
                    ),
                    store,
                )
                _print(
                    {
                        "inserted": result.inserted,
                        "kind": result.artifact.event.kind.value,
                        "result": "finalized_session",
                        "signals": result.artifact.signal_count,
                        "watches": result.artifact.watch_count,
                    }
                )
            case "reconcile-session":
                report = reconcile_us_session_command(
                    ReconcileUsSessionCommand(
                        _sources(args.opportunities, args.signals),
                        args.session_date,
                        clock(),
                        args.output,
                        args.terminal_artifact,
                    ),
                    store,
                )
                _print(
                    {
                        "acknowledged": report.acknowledged_count,
                        "complete": report.complete,
                        "expected": report.expected_count,
                        "pending": report.pending_count,
                        "result": "reconciled_session",
                        "suppressed": report.suppressed_count,
                    }
                )
            case "project-kr-cycle":
                result = project_kr_source_cycle_incident(
                    KrThemeStore(args.source_database),
                    store,
                    KrSourceCycleDeliveryRequest(
                        collection_cycle_id=args.collection_cycle_id,
                        projected_at=clock(),
                    ),
                )
                _print(
                    {
                        "examined": result.examined,
                        "inserted": result.inserted,
                        "replayed": result.replayed,
                        "result": "projected_kr_source_incident",
                    }
                )
            case "redrive":
                result = redrive_timeout_dead_letter(
                    store,
                    HermesDeliveryRedriveRequest(
                        dead_letter_transition_id=args.dead_letter_transition_id,
                    ),
                )
                _print(
                    {
                        "inserted": result.inserted,
                        "replayed": result.replayed,
                        "result": "redriven",
                    }
                )
            case "query":
                result = HermesAgentQueryService(store).query(args.symbol, observed_at=args.observed_at)
                _print(
                    {
                        "instrument_id": result.instrument_id,
                        "opinion_count": len(result.opinions),
                        "opinions": [opinion.model_dump(mode="json") for opinion in result.opinions],
                        "result": "queried",
                    }
                )
            case unreachable:
                assert_never(unreachable)
    except (
        InvalidHermesProjectionSourceError,
        InvalidHermesDeliveryRedriveError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidKrSourceCycleDeliveryError,
        InvalidHermesDeliveryStoreError,
        InvalidHermesQueryError,
        InvalidPrivateStableReportError,
        InvalidUsSessionDeliveryReconciliationError,
        InvalidUsSessionDeliveryTerminalError,
        sqlite3.DatabaseError,
        ValidationError,
    ):
        _print({"reason": "invalid_projection_source", "result": "blocked"})
        return 2
    return 0


def _datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed


def _sources(opportunities: Path, signals: Path) -> HermesProjectionSources:
    return HermesProjectionSources(
        opportunity_outbox=opportunities,
        signal_outbox=signals,
    )


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid date") from error


def _print(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
