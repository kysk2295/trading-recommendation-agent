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
from trading_agent.kr_theme_research_registration import (
    InvalidKrThemeResearchRegistrationError,
    register_kr_theme_research_manifest,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_research_registration_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme Opportunity 전략 계보를 전역 ledger에 사전등록")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = register_kr_theme_research_manifest(
            args.manifest,
            ExperimentLedgerStore(args.database),
        )
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeResearchRegistrationError,
        OSError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        ValueError,
    ):
        _write_report(
            args.output_dir,
            result="blocked",
            details=("immutable KR hypothesis 또는 strategy version을 등록하지 못했습니다",),
        )
        return 1

    _write_report(
        args.output_dir,
        result="ready",
        details=(
            _created_reused("hypothesis", result.hypotheses_created),
            _created_reused("strategy version", result.versions_created),
            "operating mode: shadow",
        ),
    )
    return 0


def _created_reused(label: str, created: int) -> str:
    return f"{label} 신규/재사용: {created}/{1 - created}"


def _write_report(
    output_dir: Path,
    *,
    result: str,
    details: tuple[str, ...],
) -> None:
    lines = (
        "# KR theme research preregistration",
        "",
        "> KR Opportunity Manager의 shadow 연구 계보만 등록하며 TradeSignal이나 주문 권한을 만들지 않습니다.",
        "",
        f"- 결과: {result}",
        *(f"- {detail}" for detail in details),
        "- external mutation: 0",
        "",
    )
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
