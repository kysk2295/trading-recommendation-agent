#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_kr_theme_research_chain_rollover.py --help
# 3. Or make executable and run:
#      chmod +x run_kr_theme_research_chain_rollover.py
#      ./run_kr_theme_research_chain_rollover.py --help
# ──────────────────

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
from trading_agent.kr_theme_research_chain_rollover import (
    InvalidKrThemeResearchChainRolloverError,
    KrThemeResearchChainRolloverRequest,
    prepare_kr_theme_research_chain_rollover,
)
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_research_rollover_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="이전 immutable KR rollover bundle을 새 clean code SHA로 이관",
    )
    parser.add_argument("--previous-bundle", type=Path, required=True)
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
        result = prepare_kr_theme_research_chain_rollover(
            KrThemeResearchChainRolloverRequest(
                experiment_ledger=ExperimentLedgerStore(args.database),
                previous_bundle_path=args.previous_bundle,
                output_dir=args.output_dir,
                code_version=args.code_version,
                recorded_at=timestamp,
            )
        )
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeResearchChainRolloverError,
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
                "# KR theme research chain rollover",
                "",
                "> 이전 exact bundle과 ledger를 새 clean code SHA에 결속합니다.",
                "",
                *(f"- {detail}" for detail in details),
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
