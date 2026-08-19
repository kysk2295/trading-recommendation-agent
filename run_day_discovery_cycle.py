from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.day_discovery_loop import DayDiscoveryEvidenceView, DayDiscoveryLoop, DayDiscoveryLoopConfig
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.heavy_empirical_lease import heavy_empirical_lease
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.research_agent_service_config import load_research_agent_service_config
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    LlmProposalClient,
    ResearcherContextInput,
    StructuredHypothesisGenerator,
    load_private_canonical_llm_response,
    load_private_canonical_researcher_context,
)
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
    build_researcher_context,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore


class CalendarSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    market_id: MarketId
    session_date: dt.date
    next_completed_bar_at: dt.datetime

    @model_validator(mode="after")
    def aware_next_bar(self):
        if self.next_completed_bar_at.tzinfo is None:
            raise ValueError("calendar_time_naive")
        return self


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded AI Day Discovery research cycle")
    parser.add_argument("--market", required=True, choices=tuple(item.value for item in MarketId))
    parser.add_argument("--evidence-view", required=True, type=Path)
    parser.add_argument("--calendar-snapshot", required=True, type=Path)
    parser.add_argument("--experiment-ledger", required=True, type=Path)
    parser.add_argument("--generated-artifact-root", required=True, type=Path)
    parser.add_argument("--receipt-root", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--max-drafts", type=int, default=3)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    proposal_client: LlmProposalClient | None = None,
    context_input: ResearcherContextInput | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> int:
    try:
        args = parse_args(argv)
        if not 1 <= args.max_drafts <= 3:
            raise ValueError("max_drafts_out_of_range")
        view = _private_canonical_model(args.evidence_view, DayDiscoveryEvidenceView)
        calendar = _private_canonical_model(args.calendar_snapshot, CalendarSnapshot)
        market = MarketId(args.market)
        if (
            view.market_id is not market
            or calendar.market_id is not market
            or calendar.next_completed_bar_at != view.first_eligible_completed_bar_at
        ):
            raise ValueError("market_calendar_mismatch")
        receipts = ResearcherReceiptStore(args.receipt_root.resolve(strict=False))
        stored_context = load_private_canonical_researcher_context(args.context)
        source = context_input or stored_context
        client = proposal_client
        runtime_path = Path(sys.executable)
        service = None
        if args.config is not None:
            service = load_research_agent_service_config(args.config)
        if client is None:
            if service is None:
                raise ValueError("research_os_config_required")
            systematic = service.systematic
            runtime_path = systematic.python_executable
            if systematic.response_fixture is not None:
                client = FixtureLlmProposalClient(
                    load_private_canonical_llm_response(systematic.response_fixture)
                )
            elif systematic.hermes_executable is not None:
                client = HermesCliProposalClient(
                    systematic.hermes_executable,
                    systematic.model_id,
                    systematic.provider_id,
                )
            else:
                raise ValueError("day_discovery_provider_required")
        elif service is not None:
            runtime_path = service.systematic.python_executable
        ledger = ExperimentLedgerStore(args.experiment_ledger.resolve(strict=False))
        context = build_researcher_context(source, ExperimentLedgerReader(ledger.path))
        runtime = resolve_generated_strategy_runtime(runtime_path)
        artifacts = GeneratedStrategyArtifactStore(args.generated_artifact_root.resolve(strict=False), runtime)
        cycle_clock = clock or (lambda: dt.datetime.now(dt.UTC))
        generator = StructuredHypothesisGenerator(
            client, receipts,
            cycle_clock,
        )
        pipeline = ResearcherPipeline(
            ResearcherPipelineServices(generator, DeterministicHypothesisCritic(max_free_parameters=4)),
            ResearcherPipelineStores(ledger, receipts, artifacts),
            ResearcherPipelineArtifacts(
                args.generated_artifact_root.resolve(strict=False) / "manifests",
                args.generated_artifact_root.resolve(strict=False) / "queue",
            ),
        )
        loop = DayDiscoveryLoop(
            DayDiscoveryLoopConfig(
                pipeline=pipeline,
                sandbox=GeneratedStrategySandbox(
                    runtime, args.generated_artifact_root.resolve(strict=False) / "sandbox", view.resource_limits
                ),
                max_drafts=args.max_drafts,
                clock=cycle_clock,
            )
        )
        with heavy_empirical_lease(ledger.path):
            result = loop.run(view, context)
        print(
            json.dumps(
                {
                    "admission_id": result.admission_id,
                    "attempt_ids": result.attempt_ids,
                    "capsule_id": result.capsule_id,
                    "cycle_id": result.cycle_id,
                    "family_id": result.family_id,
                    "hypothesis_version_id": result.hypothesis_version_id,
                    "terminal_reason": result.terminal_reason,
                },
                ensure_ascii=True, separators=(",", ":"), sort_keys=True,
            )
        )
        return 0
    except (
        InvalidPrivateImmutableFileError,
        KeyError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return 1


def _private_canonical_model[ModelT: BaseModel](
    path: Path, model_type: type[ModelT]
) -> ModelT:
    payload = read_private_text(path.expanduser().absolute())
    model = model_type.model_validate_json(payload)
    if payload != canonical_model_json(model):
        raise ValueError("private_input_noncanonical")
    return model


if __name__ == "__main__":
    raise SystemExit(main())
