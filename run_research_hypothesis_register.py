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
from trading_agent.private_report import write_private_report
from trading_agent.research_hypothesis_registration import (
    InvalidResearchHypothesisManifestError,
    register_research_hypothesis_manifest,
)

REPORT_NAME = "research_hypothesis_registration_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="연구 출처가 결합된 가설을 로컬 전역 ledger에 사전등록")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = register_research_hypothesis_manifest(
            args.manifest,
            ExperimentLedgerStore(args.database),
        )
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidResearchHypothesisManifestError,
        OSError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        ValueError,
    ):
        _write_report(
            args.output_dir,
            result="blocked",
            details=("immutable research source 또는 hypothesis card를 확인하지 못했습니다", "external mutation: 0"),
        )
        return 1

    _write_report(
        args.output_dir,
        result="ready",
        details=(
            _created_reused("research source", result.sources_created, result.sources_total),
            _created_reused("hypothesis card", result.cards_created, result.cards_total),
            "external mutation: 0",
        ),
    )
    return 0


def _created_reused(label: str, created: int, total: int) -> str:
    return f"{label} 신규/재사용: {created}/{total - created}"


def _write_report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    lines = (
        "# Research hypothesis preregistration",
        "",
        "> 출처 계보를 기록하는 로컬 연구 등록이며 전략 버전, trial, 주문 또는 자동 승격을 만들지 않습니다.",
        "",
        f"- 결과: {result}",
        *(f"- {detail}" for detail in details),
        "",
    )
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
