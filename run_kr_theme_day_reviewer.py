#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.kr_theme_day_review_store import (
    InvalidKrThemeDayReviewStoreError,
    KrThemeDayReviewStore,
)
from trading_agent.kr_theme_day_reviewer import (
    InvalidKrThemeDayReviewError,
    KrThemeDayReviewRequest,
    KrThemeDayReviewSources,
    review_kr_theme_day_strategy,
)
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.kr_theme_day_trial_terminal_store import (
    InvalidKrThemeDayTrialTerminalStoreError,
    KrThemeDayTrialTerminalStore,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_reviewer_ko.md"
KST = ZoneInfo("Asia/Seoul")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day terminal evidence를 독립 Reviewer로 평가")
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--entry-store", type=Path, required=True)
    parser.add_argument("--exit-store", type=Path, required=True)
    parser.add_argument("--terminal-store", type=Path, required=True)
    parser.add_argument("--review-store", type=Path, required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--as-of-session", type=_session_date, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    reviewed_at: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(KST) if reviewed_at is None else reviewed_at
    try:
        result = review_kr_theme_day_strategy(
            KrThemeDayReviewSources(
                ExperimentLedgerReader(args.experiment_ledger),
                KrThemeDayShadowEntryStore(args.entry_store),
                KrThemeDayShadowExitStore(args.exit_store),
                KrThemeDayTrialTerminalStore(args.terminal_store),
                KrThemeDayReviewStore(args.review_store),
            ),
            KrThemeDayReviewRequest(
                strategy_version=args.strategy_version,
                as_of_session=args.as_of_session,
                reviewed_at=timestamp,
            ),
        )
    except _CLI_ERRORS:
        return _reported_exit(args.output_dir, ("result: blocked_source",), 1)
    event = result.event
    return _reported_exit(
        args.output_dir,
        (
            "result: completed",
            f"created: {str(result.created).lower()}",
            f"action: {event.action.value}",
            f"completed_sessions: {event.completed_sessions}",
            f"censored_sessions: {event.censored_sessions}",
            f"failed_sessions: {event.failed_sessions}",
            f"completed_trades: {event.completed_trades}",
            f"policy_blockers: {','.join(event.blockers) if event.blockers else 'none'}",
        ),
        0,
    )


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("as-of session은 YYYY-MM-DD 형식이어야 합니다") from error


def _reported_exit(output_dir: Path, details: tuple[str, ...], exit_code: int) -> int:
    lines = (
        "# KR Theme Day Independent Reviewer",
        "",
        "> local immutable evidence만 읽는 독립 평가 결과입니다.",
        "",
        *(f"- {detail}" for detail in details),
        "- automatic state change: false",
        "- order authority change: false",
        "- allocation change: false",
        "- external account/order mutation: 0",
        "",
    )
    try:
        write_private_report(output_dir / REPORT_NAME, "\n".join(lines))
    except OSError:
        return 2
    return exit_code


_CLI_ERRORS = (
    InvalidExperimentLedgerSourceError,
    InvalidKrThemeDayReviewError,
    InvalidKrThemeDayReviewStoreError,
    InvalidKrThemeDayShadowEntryStoreError,
    InvalidKrThemeDayShadowExitStoreError,
    InvalidKrThemeDayTrialTerminalStoreError,
    OSError,
    sqlite3.Error,
    TypeError,
    UnsupportedExperimentLedgerSchemaError,
    ValidationError,
    ValueError,
)


if __name__ == "__main__":
    raise SystemExit(main())
