#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_researcher_propose.py --help
# 3. Or make executable and run:
#      chmod +x run_researcher_propose.py && ./run_researcher_propose.py --help
# ─────────────────

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_runtime import (
    GeneratedStrategyRuntimeError,
    resolve_generated_strategy_runtime,
)
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError
from trading_agent.private_report import write_private_report
from trading_agent.research_hypothesis_registration import InvalidResearchHypothesisManifestError
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    ResearcherLlmError,
    StructuredHypothesisGenerator,
    load_researcher_context_input,
)
from trading_agent.researcher_pipeline import (
    AcceptedResearchProposal,
    DroppedResearchProposal,
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineError,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
    build_researcher_context,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore, ResearcherReceiptStoreError
from trading_agent.source_driven_hypothesis_queue_models import InvalidSourceDrivenHypothesisQueueError

REPORT_NAME = "researcher_propose_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and preregister one bounded research hypothesis")
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--strategy-root", type=Path, required=True)
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--queue-root", type=Path, required=True)
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument("--response-fixture", type=Path)
    provider.add_argument("--hermes-executable", type=Path)
    parser.add_argument("--model-id", default="hermes-researcher-v1")
    parser.add_argument("--provider-id")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = load_researcher_context_input(args.context)
        if args.response_fixture is not None:
            client = FixtureLlmProposalClient(args.response_fixture.read_bytes())
        elif args.hermes_executable is not None and args.provider_id is not None:
            client = HermesCliProposalClient(args.hermes_executable, args.model_id, args.provider_id)
        else:
            raise ResearcherPipelineError
        receipts = ResearcherReceiptStore(args.receipt_root)
        ledger = ExperimentLedgerStore(args.experiment_ledger)
        strategies = GeneratedStrategyArtifactStore(
            args.strategy_root.resolve(strict=False),
            resolve_generated_strategy_runtime(args.python_executable),
        )
        context = build_researcher_context(source, ExperimentLedgerReader(ledger.path))
        result = ResearcherPipeline(
            ResearcherPipelineServices(
                StructuredHypothesisGenerator(client, receipts, lambda: dt.datetime.now(dt.UTC)),
                DeterministicHypothesisCritic(max_free_parameters=4),
            ),
            ResearcherPipelineStores(ledger, receipts, strategies),
            ResearcherPipelineArtifacts(args.manifest_root, args.queue_root),
        ).run(context, max_attempts=args.max_attempts)
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        GeneratedStrategyArtifactError,
        GeneratedStrategyRuntimeError,
        InvalidExperimentLedgerSourceError,
        InvalidPrivateImmutableFileError,
        InvalidResearchHypothesisManifestError,
        InvalidSourceDrivenHypothesisQueueError,
        OSError,
        ResearcherLlmError,
        ResearcherPipelineError,
        ResearcherReceiptStoreError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        ValueError,
    ):
        _report(args.output_dir, "blocked", ("proposal_or_evidence_invalid",))
        return 1
    match result:
        case AcceptedResearchProposal(
            proposal=proposal,
            strategy_artifact=strategy_artifact,
            queue_path=queue_path,
        ):
            _report(
                args.output_dir,
                "ready",
                (
                    f"hypothesis_id: {proposal.card.hypothesis.hypothesis_id}",
                    f"prompt_sha256: {proposal.llm_receipt.prompt_sha256}",
                    f"response_sha256: {proposal.llm_receipt.response_sha256}",
                    f"strategy_artifact_id: {strategy_artifact.artifact.artifact_id}",
                    f"strategy_artifact: {strategy_artifact.source_path.name}",
                    f"queue_artifact: {queue_path.name}",
                ),
            )
            return 0
        case DroppedResearchProposal(critiques=critiques):
            _report(args.output_dir, "dropped", (f"blocked_attempts: {len(critiques)}",))
            return 1
        case unexpected:
            assert_never(unexpected)


def _report(output_dir: Path, result: str, details: tuple[str, ...]) -> None:
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# Researcher proposal",
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
