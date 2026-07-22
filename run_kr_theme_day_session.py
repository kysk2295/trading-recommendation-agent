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

from pydantic import ValidationError

from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_theme_day_onboarding import (
    InvalidKrThemeDayOpportunityOnboardingError,
    KrThemeDayOpportunityOnboardingRequest,
    onboard_kr_theme_day_opportunity,
    require_exact_kr_theme_day_onboarding,
)
from trading_agent.kr_theme_day_onboarding_models import (
    load_kr_theme_day_onboarding_receipt,
    onboarding_receipt_path,
)
from trading_agent.kr_theme_day_session_audit import InvalidKrThemeDaySessionAuditError
from trading_agent.kr_theme_day_session_evidence import InvalidKrThemeDaySessionEvidenceError
from trading_agent.kr_theme_day_session_manifest import (
    InvalidKrThemeDaySessionManifestError,
    KrThemeDaySessionPaths,
    load_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_session_supervisor import (
    CommandRunner,
    InvalidKrThemeDaySessionSupervisorError,
    KrThemeDaySessionRuntime,
    KrThemeDaySessionTickResult,
    run_kr_theme_day_session_tick,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_session_ko.md"
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day restartable read-only/shadow session tick")
    commands = parser.add_subparsers(dest="command", required=True)
    onboard = commands.add_parser("onboard")
    onboard.add_argument("--manifest", type=Path, required=True)
    onboard.add_argument("--trial-id", required=True)
    onboard.add_argument("--opportunity-id", required=True)
    onboard.add_argument("--output-dir", type=Path, required=True)
    _path_arguments(onboard)
    tick = commands.add_parser("tick")
    tick.add_argument("--manifest", type=Path, required=True)
    tick.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    runner: CommandRunner | None = None,
) -> int:
    args = parse_args(argv)
    try:
        if args.command == "onboard":
            paths = _paths(args)
            result = onboard_kr_theme_day_opportunity(
                KrThemeDayOpportunityOnboardingRequest(
                    manifest_path=args.manifest.absolute(),
                    paths=paths,
                    trial_id=args.trial_id,
                    opportunity_id=args.opportunity_id,
                    onboarded_at=_onboarded_at(args.manifest, clock),
                )
            )
            _write_onboarding_report(args.output_dir, result.created)
            return 0
        if args.command != "tick":
            raise InvalidKrThemeDaySessionSupervisorError
        manifest = load_kr_theme_day_session_manifest(args.manifest)
        require_exact_kr_theme_day_onboarding(args.manifest, manifest)
        _require_calendar(manifest.paths.calendar_store, manifest.calendar_snapshot_id, manifest.session_date)
        now = clock()
        runtime = (
            KrThemeDaySessionRuntime.production(clock=clock)
            if runner is None
            else KrThemeDaySessionRuntime.production(runner=runner, clock=clock)
        )
        result = run_kr_theme_day_session_tick(manifest, now, runtime)
    except (
        InvalidKisKrSessionCalendarStoreError,
        InvalidKrThemeDaySessionAuditError,
        InvalidKrThemeDaySessionEvidenceError,
        InvalidKrThemeDaySessionManifestError,
        InvalidKrThemeDayOpportunityOnboardingError,
        InvalidKrThemeDaySessionSupervisorError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        if args.command == "onboard":
            _write_onboarding_report(args.output_dir, None)
        if args.command == "tick":
            _write_report(args.output_dir, None)
        return 1
    _write_report(args.output_dir, result)
    return 0 if result.blocked_phase is None else 1


def _path_arguments(parser: argparse.ArgumentParser) -> None:
    for name in (
        "experiment-ledger",
        "calendar-store",
        "opportunity-outbox",
        "receipt-store",
        "entry-store",
        "delivery-database",
        "exit-store",
        "terminal-store",
        "review-store",
        "audit-store",
        "output-root",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--intraday-fixture-manifest", type=Path)
    parser.add_argument("--eod-fixture-manifest", type=Path)


def _paths(args: argparse.Namespace) -> KrThemeDaySessionPaths:
    return KrThemeDaySessionPaths(
        experiment_ledger=args.experiment_ledger.absolute(),
        calendar_store=args.calendar_store.absolute(),
        opportunity_outbox=args.opportunity_outbox.absolute(),
        receipt_store=args.receipt_store.absolute(),
        entry_store=args.entry_store.absolute(),
        delivery_store=args.delivery_database.absolute(),
        exit_store=args.exit_store.absolute(),
        terminal_store=args.terminal_store.absolute(),
        review_store=args.review_store.absolute(),
        audit_store=args.audit_store.absolute(),
        output_root=args.output_root.absolute(),
        intraday_fixture_manifest=_absolute(args.intraday_fixture_manifest),
        eod_fixture_manifest=_absolute(args.eod_fixture_manifest),
    )


def _require_calendar(path: Path, snapshot_id: str, session_date: dt.date) -> None:
    matches = tuple(
        snapshot for snapshot in KisKrSessionCalendarStore(path).snapshots() if snapshot.snapshot_id == snapshot_id
    )
    if len(matches) != 1:
        raise InvalidKrThemeDaySessionSupervisorError
    days = tuple(day for day in matches[0].payload.days if day.session_date == session_date)
    if len(days) != 1 or not days[0].open_day or not days[0].business_day or not days[0].trading_day:
        raise InvalidKrThemeDaySessionSupervisorError


def _absolute(path: Path | None) -> Path | None:
    return None if path is None else path.absolute()


def _onboarded_at(manifest_path: Path, clock: Clock) -> dt.datetime:
    receipt_path = onboarding_receipt_path(manifest_path)
    if receipt_path.exists() or receipt_path.is_symlink():
        return load_kr_theme_day_onboarding_receipt(receipt_path).onboarded_at
    return clock()


def _write_report(output_dir: Path, result: KrThemeDaySessionTickResult | None) -> None:
    status = "blocked" if result is None or result.blocked_phase is not None else "complete"
    completed = 0 if result is None else len(result.completed_phases)
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day session tick",
                "",
                "> one-shot restartable control; KIS GET-only와 local shadow child만 직렬 실행합니다.",
                "",
                f"- result: {status}",
                f"- completed phase count: {completed}",
                "- order authority: false",
                "- domestic account endpoint: false",
                "- external account/order mutation: 0",
                "",
            )
        ),
    )


def _write_onboarding_report(output_dir: Path, created: bool | None) -> None:
    status = "blocked" if created is None else "complete"
    created_count = 0 if created is None else int(created)
    reused_count = 0 if created is None else int(not created)
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day Opportunity onboarding",
                "",
                "> pre-open trial과 fresh same-cycle Opportunity의 immutable local binding입니다.",
                "",
                f"- result: {status}",
                f"- manifest created/reused: {created_count}/{reused_count}",
                "- order authority: false",
                "- domestic account endpoint: false",
                "- external account/order mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
