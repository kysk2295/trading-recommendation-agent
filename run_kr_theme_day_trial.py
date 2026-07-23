#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_theme_day_trial import (
    InvalidKrThemeDayTrialError,
    KrThemeDayTrialRegistrationRequest,
    register_kr_theme_day_shadow_trial,
    start_kr_theme_day_shadow_trial,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_trial_ko.md"
KST = ZoneInfo("Asia/Seoul")
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day local-only shadow trial control")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--strategy-version", required=True)
    register.add_argument("--code-version", required=True)
    register.add_argument("--opportunity-strategy-version", required=True)
    register.add_argument("--session-date", required=True)
    register.add_argument("--registered-at", required=True)
    register.add_argument("--calendar-store", type=Path, required=True)
    _paths(register)
    start = commands.add_parser("start")
    start.add_argument("--trial-id", required=True)
    start.add_argument("--occurred-at", required=True)
    _paths(start)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    details: tuple[str, ...] = ()
    try:
        ledger = ExperimentLedgerStore(args.database)
        if args.command == "register":
            registered_at = dt.datetime.fromisoformat(args.registered_at)
            calendar_snapshot = _calendar_snapshot(args.calendar_store, registered_at)
            registered_at = _causal_registration_time(registered_at, calendar_snapshot.payload.observed_at)
            result = register_kr_theme_day_shadow_trial(
                ledger,
                KrThemeDayTrialRegistrationRequest(
                    strategy_version=args.strategy_version,
                    code_version=args.code_version,
                    session_date=dt.date.fromisoformat(args.session_date),
                    registered_at=registered_at,
                    calendar_snapshot=calendar_snapshot,
                    opportunity_strategy_version=args.opportunity_strategy_version,
                ),
                clock=clock,
            )
            details = (
                _created_reused("trial", result.created),
                f"trial id: {result.registration.trial_id}",
                f"calendar snapshot: {calendar_snapshot.snapshot_id}",
                "operating mode: shadow",
            )
        if args.command == "start":
            event = start_kr_theme_day_shadow_trial(
                ledger,
                args.trial_id,
                dt.datetime.fromisoformat(args.occurred_at),
            )
            details = (
                _created_reused("event", event.created),
                f"event kind: {event.event.event_kind.value}",
                "operating mode: shadow",
            )
        if args.command not in {"register", "start"}:
            raise InvalidKrThemeDayTrialError
    except (
        InvalidKisKrSessionCalendarStoreError,
        InvalidKrThemeDayTrialError,
        OSError,
        sqlite3.Error,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, "blocked", ("trial lineage 또는 입력 계약을 확인하지 못했습니다",))
        return 1
    _write_report(args.output_dir, "ready", details)
    return 0


def _paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


def _calendar_snapshot(path: Path, registered_at: dt.datetime) -> KrSessionCalendarSnapshot:
    if registered_at.tzinfo is None or registered_at.utcoffset() is None:
        raise InvalidKrThemeDayTrialError
    matches = tuple(
        snapshot
        for snapshot in KisKrSessionCalendarStore(path).snapshots()
        if snapshot.payload.base_date == registered_at.astimezone(KST).date()
    )
    if len(matches) != 1:
        raise InvalidKrThemeDayTrialError
    return matches[0]


def _causal_registration_time(requested: dt.datetime, observed_at: dt.datetime) -> dt.datetime:
    if observed_at <= requested:
        return requested
    requested_utc = requested.astimezone(dt.UTC)
    observed_utc = observed_at.astimezone(dt.UTC)
    if requested_utc.replace(microsecond=0) != observed_utc.replace(microsecond=0):
        raise InvalidKrThemeDayTrialError
    return observed_at


def _created_reused(label: str, created: bool) -> str:
    return f"{label} 신규/재사용: {int(created)}/{int(not created)}"


def _write_report(output_dir: Path, result: str, details: tuple[str, ...]) -> None:
    lines = (
        "# KR theme day shadow trial",
        "",
        "> local append-only research lineage only; provider, broker, account와 주문을 호출하지 않습니다.",
        "",
        f"- 결과: {result}",
        *(f"- {detail}" for detail in details),
        "- order authority: false",
        "- external mutation: 0",
        "",
    )
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
