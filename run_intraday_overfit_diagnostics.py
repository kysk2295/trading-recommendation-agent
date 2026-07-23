#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.intraday_overfit_diagnostics import (
    IntradayOverfitDiagnosticsRequest,
    diagnose_intraday_overfit,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    InvalidIntradayOverfitDiagnosticsError,
)
from trading_agent.intraday_research_artifacts import (
    InvalidIntradayResearchArtifactError,
    load_intraday_experiment_artifact,
)
from trading_agent.intraday_research_reviewer import (
    InvalidIntradayResearchReviewError,
    load_intraday_review_artifact,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "intraday_overfit_diagnostics_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute query-only DSR and CSCV-PBO diagnostics from exact "
            "intraday OOS outcome traces"
        )
    )
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument(
        "--experiment-artifact",
        type=Path,
        action="append",
        required=True,
    )
    parser.add_argument(
        "--review-artifact",
        type=Path,
        action="append",
        required=True,
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reviewed_at = dt.datetime.fromisoformat(args.reviewed_at)
        artifact, created = diagnose_intraday_overfit(
            IntradayOverfitDiagnosticsRequest(
                ledger=ExperimentLedgerReader(args.experiment_ledger),
                experiments=tuple(
                    load_intraday_experiment_artifact(path)
                    for path in args.experiment_artifact
                ),
                reviews=tuple(
                    load_intraday_review_artifact(path)
                    for path in args.review_artifact
                ),
                artifact_root=args.artifact_root,
                reviewed_at=reviewed_at,
            )
        )
    except (
        InvalidExperimentLedgerSourceError,
        InvalidIntradayOverfitDiagnosticsError,
        InvalidIntradayResearchArtifactError,
        InvalidIntradayResearchReviewError,
        OSError,
        sqlite3.Error,
        TypeError,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        ValueError,
    ):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Intraday overfit diagnostics\n\n"
            "- result: blocked\n"
            "- automatic state change: false\n"
            "- order authority change: false\n"
            "- allocation change: false\n"
            "- external mutation: 0\n",
        )
        return 1
    statistics = artifact.payload.statistics
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Intraday overfit diagnostics\n\n"
        f"- result: {statistics.status.value}\n"
        f"- candidate variants: {len(statistics.candidates)}\n"
        "- conservative lane trial count: "
        f"{statistics.total_lane_historical_trials}\n"
        f"- blockers: {len(statistics.blockers)}\n"
        "- DSR available: "
        f"{'yes' if statistics.deflated_sharpe_probability is not None else 'no'}\n"
        "- PBO available: "
        f"{'yes' if statistics.pbo_probability is not None else 'no'}\n"
        f"- diagnostics artifact created: {'yes' if created else 'no'}\n"
        "- automatic state change: false\n"
        "- order authority change: false\n"
        "- allocation change: false\n"
        "- external mutation: 0\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
