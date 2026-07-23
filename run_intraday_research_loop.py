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

from trading_agent.experiment_ledger_bootstrap import InvalidExperimentLedgerBootstrapSourceError
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.intraday_research_artifacts import InvalidIntradayResearchArtifactError
from trading_agent.intraday_research_data_gate import InvalidIntradayResearchDataError
from trading_agent.intraday_research_loop import (
    IntradayResearchLoopError,
    IntradayResearchLoopPaths,
    run_intraday_research_loop,
)
from trading_agent.intraday_research_loop_models import (
    InvalidIntradayResearchManifestError,
    load_intraday_research_manifest,
)
from trading_agent.intraday_research_reviewer import InvalidIntradayResearchReviewError
from trading_agent.intraday_research_trial import IntradayHistoricalTrialError
from trading_agent.intraday_trial_design import InvalidIntradayTrialDesignError
from trading_agent.lane_registry_store import InvalidLaneRegistrySourceError, UnsupportedLaneRegistrySchemaError
from trading_agent.private_report import write_private_report
from trading_agent.replay import BoundedReplaySourceError
from trading_agent.source_backed_intraday_design import InvalidSourceBackedIntradayDesignError
from trading_agent.source_driven_hypothesis_queue_models import InvalidSourceDrivenHypothesisQueueError

REPORT_NAME = "intraday_research_loop_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded local intraday research and review loop")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--source-queue-artifact", type=Path)
    parser.add_argument("--data-foundation-manifest", type=Path, action="append")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = load_intraday_research_manifest(args.manifest)
        result = run_intraday_research_loop(
            manifest,
            IntradayResearchLoopPaths(
                input_csv=args.input_csv,
                lane_registry=args.lane_registry,
                experiment_ledger=args.experiment_ledger,
                artifact_root=args.artifact_root,
                review_root=args.review_root,
                source_queue_artifact=args.source_queue_artifact,
                data_foundation_manifests=tuple(args.data_foundation_manifest or ()),
            ),
        )
    except (
        BoundedReplaySourceError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerBootstrapSourceError,
        InvalidExperimentLedgerSourceError,
        InvalidIntradayResearchArtifactError,
        InvalidIntradayResearchDataError,
        InvalidIntradayResearchManifestError,
        InvalidIntradayResearchReviewError,
        InvalidIntradayTrialDesignError,
        IntradayHistoricalTrialError,
        IntradayResearchLoopError,
        InvalidLaneRegistrySourceError,
        OSError,
        InvalidSourceBackedIntradayDesignError,
        InvalidSourceDrivenHypothesisQueueError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        UnsupportedLaneRegistrySchemaError,
        ValidationError,
        ValueError,
    ):
        write_private_report(
            args.output_dir / REPORT_NAME,
            "# Intraday research loop\n\n- result: blocked\n- external mutation: 0\n",
        )
        return 1
    decisions = ", ".join(decision.value for decision in result.decisions)
    write_private_report(
        args.output_dir / REPORT_NAME,
        "# Intraday research loop\n\n"
        + "- result: ready\n"
        + f"- trials: {result.trials_total}\n"
        + f"- experiment artifacts created: {result.experiment_artifacts_created}\n"
        + f"- review artifacts created: {result.review_artifacts_created}\n"
        + f"- reviewer decisions: {decisions}\n"
        + "- automatic state change: false\n"
        + "- external mutation: 0\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
