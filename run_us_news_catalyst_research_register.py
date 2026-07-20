#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
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
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.us_news_catalyst_research_registration import (
    InvalidUsNewsCatalystResearchRegistrationError,
    register_us_news_catalyst_research_manifest,
)

REPORT_NAME = "us_news_catalyst_research_registration_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US news-catalyst Opportunity shadow 가설과 전략 버전을 사전등록"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = register_us_news_catalyst_research_manifest(
            args.manifest,
            ExperimentLedgerStore(args.database),
        )
        _write_report(
            args.output_dir,
            result="ready",
            details=(
                _created_reused("hypothesis", result.hypotheses_created),
                _created_reused("strategy version", result.versions_created),
                f"lane: {result.strategy_lane.canonical_id}",
                "operating mode: shadow",
            ),
        )
        return 0
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidPrivateStableReportError,
        InvalidUsNewsCatalystResearchRegistrationError,
        OSError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        ValueError,
    ):
        _write_report(
            args.output_dir,
            result="blocked",
            details=("immutable hypothesis 또는 strategy version을 등록하지 못했습니다",),
        )
        return 1


def _created_reused(label: str, created: int) -> str:
    return f"{label} 신규/재사용: {created}/{1 - created}"


def _write_report(
    output_dir: Path,
    *,
    result: str,
    details: tuple[str, ...],
) -> None:
    lines = (
        "# US news-catalyst research preregistration",
        "",
        "> bounded news discovery shadow 계보만 등록하며 방향·진입가·주문 권한을 만들지 않습니다.",
        "",
        f"- 결과: {result}",
        *(f"- {detail}" for detail in details),
        "- external mutation: 0",
        "",
    )
    write_private_stable_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
