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

from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_theme_day_lifecycle_controller import (
    InvalidKrThemeDayLifecycleSourceError,
    KrThemeDayLifecycleRequest,
    control_kr_theme_day_lifecycle,
)
from trading_agent.kr_theme_day_review_store import (
    InvalidKrThemeDayReviewStoreError,
    KrThemeDayReviewStore,
)
from trading_agent.kr_theme_day_reviewer import KrThemeDayReviewSources
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

REPORT_NAME = "kr_theme_day_lifecycle_ko.md"
KST = ZoneInfo("Asia/Seoul")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="검증된 KR theme day Reviewer evidence로 next-session lifecycle을 평가"
    )
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--entry-store", type=Path, required=True)
    parser.add_argument("--exit-store", type=Path, required=True)
    parser.add_argument("--terminal-store", type=Path, required=True)
    parser.add_argument("--review-store", type=Path, required=True)
    parser.add_argument("--calendar-store", type=Path, required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--as-of-session", type=_session_date, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    decided_at: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(KST) if decided_at is None else decided_at
    ledger = ExperimentLedgerStore(args.experiment_ledger)
    try:
        calendar = _calendar_snapshot(args.calendar_store, args.as_of_session)
        sources = KrThemeDayReviewSources(
            ExperimentLedgerReader(args.experiment_ledger),
            KrThemeDayShadowEntryStore(args.entry_store),
            KrThemeDayShadowExitStore(args.exit_store),
            KrThemeDayTrialTerminalStore(args.terminal_store),
            KrThemeDayReviewStore(args.review_store),
        )
        result = control_kr_theme_day_lifecycle(
            ledger,
            sources,
            KrThemeDayLifecycleRequest(
                strategy_version=args.strategy_version,
                as_of_session=args.as_of_session,
                decided_at=timestamp,
                calendar_snapshot=calendar,
            ),
        )
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKisKrSessionCalendarStoreError,
        InvalidKrThemeDayLifecycleSourceError,
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
    ):
        _write_report(args.output_dir, ("result: blocked_source",))
        return 1
    _write_report(
        args.output_dir,
        (
            "result: completed",
            f"outcome: {result.outcome.value}",
            f"created: {str(result.created).lower()}",
            f"from_state: {_state(result.from_state)}",
            f"to_state: {_state(result.to_state)}",
            f"reason_codes: {','.join(result.reason_codes)}",
            f"policy_blockers: {','.join(result.blockers) if result.blockers else 'none'}",
        ),
    )
    return 0


def _calendar_snapshot(path: Path, as_of_session: dt.date) -> KrSessionCalendarSnapshot:
    matches = tuple(
        snapshot
        for snapshot in KisKrSessionCalendarStore(path).snapshots()
        if snapshot.payload.base_date == as_of_session
    )
    if len(matches) != 1:
        raise InvalidKisKrSessionCalendarStoreError
    return matches[0]


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("as-of session은 YYYY-MM-DD 형식이어야 합니다") from error


def _state(value: StrategyLifecycleState | None) -> str:
    return "none" if value is None else value.value


def _write_report(output_dir: Path, details: tuple[str, ...]) -> None:
    lines = (
        "# KR Theme Day Lifecycle Controller",
        "",
        "> 검증된 local evidence만 사용하는 다음 영업일 상태 평가입니다.",
        "",
        *(f"- {detail}" for detail in details),
        "- automatic champion: false",
        "- order authority change: false",
        "- allocation change: false",
        "- external account/order mutation: 0",
        "",
    )
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
