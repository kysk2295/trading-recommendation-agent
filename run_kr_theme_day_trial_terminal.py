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
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.kr_theme_day_trial_terminal import (
    InvalidKrThemeDayTrialTerminalError,
    KrThemeDayTrialTerminalStores,
    finalize_kr_theme_day_shadow_trial,
)
from trading_agent.kr_theme_day_trial_terminal_models import (
    InvalidKrThemeDayTrialTerminalModelError,
    KrThemeDayTrialTerminalRequest,
)
from trading_agent.kr_theme_day_trial_terminal_store import (
    InvalidKrThemeDayTrialTerminalStoreError,
    KrThemeDayTrialTerminalStore,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_trial_terminal_ko.md"
KST = ZoneInfo("Asia/Seoul")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day shadow trial을 local evidence로 장후 확정")
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--entry-store", type=Path, required=True)
    parser.add_argument("--exit-store", type=Path, required=True)
    parser.add_argument("--terminal-store", type=Path, required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    occurred_at: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(KST) if occurred_at is None else occurred_at
    try:
        result = finalize_kr_theme_day_shadow_trial(
            ExperimentLedgerStore(args.experiment_ledger),
            KrThemeDayTrialTerminalStores(
                KrThemeDayShadowEntryStore(args.entry_store),
                KrThemeDayShadowExitStore(args.exit_store),
                KrThemeDayTrialTerminalStore(args.terminal_store),
            ),
            KrThemeDayTrialTerminalRequest(trial_id=args.trial_id, occurred_at=timestamp),
        )
    except _CLI_ERRORS:
        return _reported_exit(args.output_dir, ("result: blocked_source",), 1)
    return _reported_exit(
        args.output_dir,
        (
            "result: completed",
            f"event_kind: {result.event.event_kind.value}",
            f"artifact_created: {str(result.artifact_created).lower()}",
            f"event_created: {str(result.event_created).lower()}",
            f"entry_count: {len(result.artifact.payload.entry_ids)}",
            f"exit_count: {len(result.artifact.payload.exit_ids)}",
            f"reason_codes: {','.join(result.event.reason_codes) if result.event.reason_codes else 'none'}",
        ),
        0,
    )


def _reported_exit(output_dir: Path, details: tuple[str, ...], exit_code: int) -> int:
    lines = (
        "# KR Theme Day Trial Terminal",
        "",
        "> local shadow evidence의 장후 terminal 결과입니다.",
        "",
        *(f"- {detail}" for detail in details),
        "- account/order authority: false",
        "- external account/order mutation: 0",
        "",
    )
    try:
        write_private_report(output_dir / REPORT_NAME, "\n".join(lines))
    except OSError:
        return 2
    return exit_code


_CLI_ERRORS = (
    ExperimentLedgerConflictError,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    InvalidKrThemeDayShadowEntryStoreError,
    InvalidKrThemeDayShadowExitStoreError,
    InvalidKrThemeDayTrialTerminalError,
    InvalidKrThemeDayTrialTerminalModelError,
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
