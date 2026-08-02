#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.autonomous_research_cycle import (
    AutonomousResearchCycleConfig,
    run_autonomous_research_cycle,
)
from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategyLimits, GeneratedStrategySandbox
from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.private_query_file import read_private_text_query_only
from trading_agent.private_report import write_private_report
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    StructuredHypothesisGenerator,
    load_researcher_context_input,
)
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore

REPORT_NAME = "autonomous_research_cycle_ko.md"


class InvalidAutonomousCycleCliResultError(RuntimeError):
    pass


class AutonomousCycleCliResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["complete", "blocked"]
    strategy_artifact_id: str | None
    trial_id: str | None
    experiment_artifact_id: str | None
    review_artifact_id: str | None
    reviewer_decision: str | None
    reason_codes: tuple[str, ...]
    lifecycle_authority: Literal[False]
    allocation_authority: Literal[False]
    order_authority: Literal[False]
    trading_mutation: Literal[0]

    @model_validator(mode="after")
    def require_status_contract(self) -> Self:
        artifacts = (
            self.strategy_artifact_id,
            self.trial_id,
            self.experiment_artifact_id,
            self.review_artifact_id,
            self.reviewer_decision,
        )
        if self.status == "complete" and (any(item is None for item in artifacts) or self.reason_codes):
            raise InvalidAutonomousCycleCliResultError
        if self.status == "blocked" and (any(item is not None for item in artifacts) or not self.reason_codes):
            raise InvalidAutonomousCycleCliResultError
        return self


def load_autonomous_cycle_cli_result(output_dir: Path) -> AutonomousCycleCliResult:
    try:
        text = read_private_text_query_only(output_dir / REPORT_NAME)
        fields: dict[str, str] = {}
        reasons: list[str] = []
        for line in text.splitlines():
            if not line.startswith("- "):
                continue
            content = line[2:]
            if ": " not in content:
                reasons.append(content)
                continue
            key, value = content.split(": ", 1)
            if key in fields:
                raise InvalidAutonomousCycleCliResultError
            fields[key] = value
        if (
            fields.get("lifecycle authority") != "false"
            or fields.get("allocation authority") != "false"
            or fields.get("order authority") != "false"
            or fields.get("trading mutation") != "0"
        ):
            raise InvalidAutonomousCycleCliResultError
        return AutonomousCycleCliResult.model_validate(
            {
                "status": fields.get("result"),
                "strategy_artifact_id": fields.get("strategy_artifact_id"),
                "trial_id": fields.get("trial_id"),
                "experiment_artifact_id": fields.get("experiment_artifact_id"),
                "review_artifact_id": fields.get("review_artifact_id"),
                "reviewer_decision": fields.get("reviewer_decision"),
                "reason_codes": tuple(sorted(set(reasons))),
                "lifecycle_authority": False,
                "allocation_authority": False,
                "order_authority": False,
                "trading_mutation": 0,
            }
        )
    except (InvalidAutonomousCycleCliResultError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidAutonomousCycleCliResultError from None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded generated-Python strategy research cycle"
    )
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--strategy-root", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--queue-root", type=Path, required=True)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--data-foundation-manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument("--response-fixture", type=Path)
    provider.add_argument("--hermes-executable", type=Path)
    parser.add_argument("--model-id", default="hermes-researcher-v1")
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--minimum-training-sessions", type=int, default=0)
    parser.add_argument("--max-bars", type=int, default=100_000)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--per-side-fee-bps", type=int, default=5)
    parser.add_argument("--per-side-slippage-bps", type=int, default=15)
    parser.add_argument("--bootstrap-samples", type=int, default=200)
    parser.add_argument("--rss-limit-gib", type=float, default=9.5)
    parser.add_argument("--wall-seconds", type=float, default=2.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = load_researcher_context_input(args.context)
        if args.response_fixture is not None:
            client = FixtureLlmProposalClient(args.response_fixture.read_bytes())
        elif args.hermes_executable is not None:
            client = HermesCliProposalClient(args.hermes_executable, args.model_id)
        else:
            raise ValueError
        runtime = resolve_generated_strategy_runtime(args.python_executable)
        receipts = ResearcherReceiptStore(args.receipt_root.resolve(strict=False))
        ledger = ExperimentLedgerStore(args.experiment_ledger.resolve(strict=False))
        strategies = GeneratedStrategyArtifactStore(
            args.strategy_root.resolve(strict=False),
            runtime,
        )
        pipeline = ResearcherPipeline(
            ResearcherPipelineServices(
                StructuredHypothesisGenerator(client, receipts, lambda: dt.datetime.now(dt.UTC)),
                DeterministicHypothesisCritic(max_free_parameters=4),
            ),
            ResearcherPipelineStores(ledger, receipts, strategies),
            ResearcherPipelineArtifacts(
                args.manifest_root.resolve(strict=False),
                args.queue_root.resolve(strict=False),
            ),
        )
        result = run_autonomous_research_cycle(
            AutonomousResearchCycleConfig(
                source=source,
                pipeline=pipeline,
                ledger=ledger,
                sandbox=GeneratedStrategySandbox(
                    runtime,
                    args.strategy_root.resolve(strict=False) / "tasks",
                    GeneratedStrategyLimits(
                        wall_seconds=args.wall_seconds,
                        rss_bytes=int(args.rss_limit_gib * 1024**3),
                    ),
                ),
                input_csv=args.input_csv,
                data_foundation_manifest=args.data_foundation_manifest,
                experiment_root=args.artifact_root.resolve(strict=False),
                review_root=args.review_root.resolve(strict=False),
                max_attempts=args.max_attempts,
                minimum_training_sessions=args.minimum_training_sessions,
                max_bars=args.max_bars,
                max_sessions=args.max_sessions,
                per_side_fee_bps=args.per_side_fee_bps,
                per_side_slippage_bps=args.per_side_slippage_bps,
                bootstrap_samples=args.bootstrap_samples,
                rss_limit_gib=args.rss_limit_gib,
            )
        )
    except (OSError, RuntimeError, TypeError, ValidationError, ValueError, sqlite3.Error):
        _report(args.output_dir, "blocked", ("cycle_or_evidence_invalid",))
        return 1
    _report(
        args.output_dir,
        "complete",
        (
            f"strategy_artifact_id: {result.accepted.strategy_artifact.artifact.artifact_id}",
            f"trial_id: {result.historical.trial_id}",
            f"experiment_artifact_id: {result.historical.experiment_artifact_id}",
            f"review_artifact_id: {result.historical.review_artifact_id}",
            f"reviewer_decision: {result.historical.decision.value}",
            f"next_feedback: {','.join(result.next_context.failure_digest.reviewer_decisions)}",
        ),
    )
    return 0


def _report(output_dir: Path, result: str, details: tuple[str, ...]) -> None:
    directory = open_private_parent(output_dir, create=True)
    try:
        require_private_directory(directory)
    finally:
        os.close(directory)
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# Autonomous generated strategy research cycle",
                "",
                f"- result: {result}",
                *(f"- {detail}" for detail in details),
                "- lifecycle authority: false",
                "- allocation authority: false",
                "- order authority: false",
                "- trading mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
