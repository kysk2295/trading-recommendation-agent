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

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.kr_theme_research_rollover import (
    InvalidKrThemeResearchRolloverError,
    prepare_kr_theme_research_rollover,
)
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_research_rollover_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="등록된 KR theme 계약을 새 clean code SHA의 shadow version으로 이관",
    )
    parser.add_argument("--opportunity-manifest", type=Path, required=True)
    parser.add_argument("--day-manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--code-version", required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(dt.UTC) if now is None else now
    try:
        result = prepare_kr_theme_research_rollover(
            experiment_ledger=ExperimentLedgerStore(args.database),
            opportunity_manifest_path=args.opportunity_manifest,
            day_manifest_path=args.day_manifest,
            policy_path=args.policy,
            output_dir=args.output_dir,
            code_version=args.code_version,
            recorded_at=timestamp,
        )
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeResearchRolloverError,
        InvalidPrivateImmutableFileError,
        OSError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, None)
        return 1
    _write_report(args.output_dir, result.versions_created)
    return 0


def _write_report(output_dir: Path, versions_created: int | None) -> None:
    details = (
        ("결과: blocked", "external mutation: 0")
        if versions_created is None
        else (
            "결과: ready",
            f"strategy versions 신규/재사용: {versions_created}/{2 - versions_created}",
            "operating mode: shadow",
            "account/order authority: false",
            "external mutation: 0",
        )
    )
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme research code-version rollover",
                "",
                "> exact registered hypothesis와 private policy만 새 clean code SHA에 결속합니다.",
                "",
                *(f"- {detail}" for detail in details),
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
