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

from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    load_alpaca_news_opportunity_evidence,
)
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_news_catalyst_opportunity_artifact import (
    load_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_research_registration import (
    load_us_news_catalyst_research_manifest,
)
from trading_agent.us_news_catalyst_reviewer import review_us_news_catalyst_trials
from trading_agent.us_news_catalyst_trial import (
    finalize_us_news_catalyst_trial,
    register_us_news_catalyst_daily_trial,
    start_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_artifact import load_us_news_catalyst_cohort
from trading_agent.us_news_catalyst_trial_models import (
    UsNewsCatalystCohortStatus,
    UsNewsCatalystDailyTrialRegistrationRequest,
)

REPORT_NAME = "us_news_catalyst_shadow_trial_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US news-catalyst 일별 shadow trial과 독립 Reviewer를 운영"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _register_parser(commands)
    _start_parser(commands)
    _finalize_parser(commands)
    _review_parser(commands)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    try:
        now = clock()
        if not _aware(now):
            raise ValueError("clock must be timezone-aware")
        match args.command:
            case "register":
                return _register(args, now)
            case "start":
                return _start(args, now)
            case "finalize":
                return _finalize(args, now)
            case "review":
                return _review(args, now)
            case _:
                raise ValueError("unsupported command")
    except (AttributeError, OSError, sqlite3.Error, ValidationError, ValueError):
        _write_report(args.output_dir, args.command, "blocked", False)
        return 1


def _register(args: argparse.Namespace, now: dt.datetime) -> int:
    manifest = load_us_news_catalyst_research_manifest(args.registration_manifest)
    result = register_us_news_catalyst_daily_trial(
        ExperimentLedgerStore(args.experiment_ledger),
        UsNewsCatalystDailyTrialRegistrationRequest(
            strategy_version=manifest.strategy_version,
            code_version=manifest.code_version,
            session_date=args.session_date,
            registered_at=now,
        ),
    )
    _write_report(args.output_dir, "register", "registered", result.created)
    return 0


def _start(args: argparse.Namespace, now: dt.datetime) -> int:
    result = start_us_news_catalyst_trial(
        ExperimentLedgerStore(args.experiment_ledger),
        args.trial_id,
        load_us_news_catalyst_opportunity_projection(args.projection),
        load_alpaca_news_opportunity_evidence(args.evidence),
        args.artifact_root,
        started_at=now,
    )
    status = result.cohort.payload.status
    _write_report(args.output_dir, "start", status.value, result.event_created)
    return 0 if status is UsNewsCatalystCohortStatus.READY else 2


def _finalize(args: argparse.Namespace, now: dt.datetime) -> int:
    result = finalize_us_news_catalyst_trial(
        ExperimentLedgerStore(args.experiment_ledger),
        args.trial_id,
        load_us_news_catalyst_cohort(args.cohort),
        args.observation_manifest,
        args.artifact_root,
        finalized_at=now,
    )
    terminal = result.outcome.payload.terminal_kind
    _write_report(args.output_dir, "finalize", terminal.value, result.event_created)
    return 0 if terminal is TrialEventKind.COMPLETED else 2


def _review(args: argparse.Namespace, now: dt.datetime) -> int:
    result = review_us_news_catalyst_trials(
        ExperimentLedgerStore(args.experiment_ledger),
        args.artifact_root,
        args.review_root,
        strategy_version=args.strategy_version,
        as_of_session=args.as_of_session,
        reviewed_at=now,
    )
    action = result.artifact.payload.action
    _write_report(args.output_dir, "review", action.value, result.created)
    return 0 if action.value == "comparison_ready" else 2


def _register_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("register", help="장전 일별 shadow trial 등록")
    parser.add_argument("--registration-manifest", type=Path, required=True)
    parser.add_argument("--session-date", type=dt.date.fromisoformat, required=True)
    _ledger_output_args(parser)


def _start_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("start", help="장중 treatment/control cohort 동결")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    _ledger_output_args(parser)


def _finalize_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("finalize", help="30분 setup observation으로 terminal 확정")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--observation-manifest", type=Path)
    parser.add_argument("--artifact-root", type=Path, required=True)
    _ledger_output_args(parser)


def _review_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("review", help="장후 독립 Reviewer 집계")
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--as-of-session", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    _ledger_output_args(parser)


def _ledger_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


def _write_report(output_dir: Path, operation: str, result: str, created: bool) -> None:
    lines = (
        "# US news-catalyst shadow trial",
        "",
        "> shadow research only; no direction, entry price, allocation, or order authority.",
        "",
        f"- operation: {operation}",
        f"- result: {result}",
        f"- ledger/artifact 신규 여부: {int(created)}",
        "- provider request: 0",
        "- credential read: 0",
        "- account read: 0",
        "- order mutation: 0",
        "",
    )
    write_private_stable_report(output_dir / REPORT_NAME, "\n".join(lines))


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


if __name__ == "__main__":
    raise SystemExit(main())
