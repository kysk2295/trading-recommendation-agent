#!/usr/bin/env -S uv run --offline --script
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

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_news_catalyst_day_session_manifest import (
    InvalidUsNewsCatalystDaySessionManifestError,
    UsNewsCatalystDaySessionIdentity,
    UsNewsCatalystDaySessionPaths,
    build_us_news_catalyst_day_session_manifest,
    load_us_news_catalyst_day_session_manifest,
    write_us_news_catalyst_day_session_manifest,
)
from trading_agent.us_news_catalyst_day_session_store import (
    InvalidUsNewsCatalystDaySessionStoreError,
    UsNewsCatalystDaySessionWriterLeaseUnavailableError,
)
from trading_agent.us_news_catalyst_day_session_supervisor import (
    CommandRunner,
    InvalidUsNewsCatalystDaySessionSupervisorError,
    UsNewsCatalystDaySessionActionStatus,
    UsNewsCatalystDaySessionRuntime,
    run_us_news_catalyst_day_session_tick,
)
from trading_agent.us_news_catalyst_research_registration import (
    load_us_news_catalyst_research_manifest,
)

REPORT_NAME = "us_news_catalyst_day_session_ko.md"
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US news-catalyst restartable daily shadow research session",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="immutable daily session manifest 생성")
    initialize.add_argument("--registration-manifest", type=Path, required=True)
    initialize.add_argument("--session-date", type=dt.date.fromisoformat, required=True)
    initialize.add_argument("--experiment-ledger", type=Path, required=True)
    initialize.add_argument("--projection-root", type=Path, required=True)
    initialize.add_argument("--evidence-root", type=Path, required=True)
    initialize.add_argument("--security-master-store", type=Path, required=True)
    initialize.add_argument("--session-root", type=Path, required=True)
    initialize.add_argument("--manifest", type=Path, required=True)
    initialize.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    initialize.add_argument("--output-dir", type=Path, required=True)
    tick = commands.add_parser("tick", help="현재 필요한 phase를 최대 하나 실행")
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
        if args.command == "init":
            return _initialize(args, clock())
        if args.command != "tick":
            raise InvalidUsNewsCatalystDaySessionSupervisorError
        production = UsNewsCatalystDaySessionRuntime.production()
        runtime = UsNewsCatalystDaySessionRuntime(
            runner=production.runner if runner is None else runner,
            clock=clock,
            source_state=production.source_state,
            action=production.action,
        )
        result = run_us_news_catalyst_day_session_tick(
            load_us_news_catalyst_day_session_manifest(args.manifest),
            clock(),
            runtime,
        )
        status = "complete" if result.phase is None else (
            "waiting" if result.event is None else result.event.status.value
        )
        phase = "none" if result.phase is None else result.phase.value
        _write_report(args.output_dir, "tick", status, phase)
        return 1 if result.action_status is UsNewsCatalystDaySessionActionStatus.BLOCKED else 0
    except (
        InvalidUsNewsCatalystDaySessionManifestError,
        InvalidUsNewsCatalystDaySessionStoreError,
        InvalidUsNewsCatalystDaySessionSupervisorError,
        UsNewsCatalystDaySessionWriterLeaseUnavailableError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, args.command, "blocked", "none")
        return 1


def _initialize(args: argparse.Namespace, created_at: dt.datetime) -> int:
    registration = load_us_news_catalyst_research_manifest(args.registration_manifest)
    root = _absolute(args.session_root)
    paths = UsNewsCatalystDaySessionPaths(
        experiment_ledger=_absolute(args.experiment_ledger),
        registration_manifest=_absolute(args.registration_manifest),
        projection_root=_absolute(args.projection_root),
        evidence_root=_absolute(args.evidence_root),
        security_master_store=_absolute(args.security_master_store),
        artifact_root=root / "artifacts",
        plan_root=root / "plans",
        profile_root=root / "profiles",
        runtime_root=root / "runtime",
        canonical_root=root / "canonical",
        feature_root=root / "features",
        receipt_root=root / "receipts",
        review_root=root / "reviews",
        audit_store=root / "audit.sqlite3",
        output_root=root / "phase-reports",
        secret_path=_absolute(args.secret_path),
    )
    manifest = build_us_news_catalyst_day_session_manifest(
        UsNewsCatalystDaySessionIdentity(
            strategy_version=registration.strategy_version,
            code_version=registration.code_version,
            session_date=args.session_date,
            created_at=created_at,
            paths=paths,
        )
    )
    created = write_us_news_catalyst_day_session_manifest(_absolute(args.manifest), manifest)
    _write_report(args.output_dir, "init", "ready", "created" if created else "replay")
    return 0


def _write_report(output_dir: Path, operation: str, status: str, phase: str) -> None:
    lines = (
        "# US news-catalyst daily session",
        "",
        "> shadow research only; Alpaca market-data GET only; no account or order authority.",
        "",
        f"- operation: {operation}",
        f"- result: {status}",
        f"- phase: {phase}",
        "- account read: 0",
        "- order mutation: 0",
        "",
    )
    write_private_stable_report(output_dir / REPORT_NAME, "\n".join(lines))


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


if __name__ == "__main__":
    raise SystemExit(main())
