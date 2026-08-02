from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from trading_agent.autonomous_research_cycle import (
    AutonomousResearchCycleConfig,
    run_autonomous_research_cycle,
)
from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategyLimits, GeneratedStrategySandbox
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
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

PROJECT = Path(__file__).resolve().parents[1]
CONTEXT = PROJECT / "examples" / "research" / "researcher-context-v1.json"
RESPONSE = PROJECT / "examples" / "research" / "researcher-response-fixture-v1.json"
INPUT = PROJECT / "examples" / "example_intraday.csv"
FOUNDATION = PROJECT / "examples" / "data" / "us-vwap-reclaim-historical-fixture-v1.json"


def test_one_cycle_proposes_executes_reviews_and_rebuilds_feedback(tmp_path: Path) -> None:
    # Given: the fixture Researcher, real sandbox, conservative evaluator, and append-only ledger.
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    receipts = ResearcherReceiptStore(tmp_path / "receipts")
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    pipeline = ResearcherPipeline(
        ResearcherPipelineServices(
            StructuredHypothesisGenerator(
                FixtureLlmProposalClient(RESPONSE.read_bytes()),
                receipts,
                lambda: dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
            ),
            DeterministicHypothesisCritic(max_free_parameters=4),
        ),
        ResearcherPipelineStores(
            ledger,
            receipts,
            GeneratedStrategyArtifactStore(tmp_path / "strategies", runtime),
        ),
        ResearcherPipelineArtifacts(tmp_path / "manifests", tmp_path / "queue"),
    )

    # When: exactly one bounded autonomous cycle runs through all public library surfaces.
    result = run_autonomous_research_cycle(
        AutonomousResearchCycleConfig(
            source=load_researcher_context_input(CONTEXT),
            pipeline=pipeline,
            ledger=ledger,
            sandbox=GeneratedStrategySandbox(
                runtime,
                tmp_path / "tasks",
                GeneratedStrategyLimits(),
            ),
            input_csv=INPUT,
            data_foundation_manifest=FOUNDATION,
            experiment_root=tmp_path / "experiments",
            review_root=tmp_path / "reviews",
            max_bars=10,
            max_sessions=1,
        )
    )

    # Then: artifact, version, terminal trial, Reviewer HOLD, and next-cycle feedback all exist.
    reader = ExperimentLedgerReader(ledger.path)
    assert result.historical.decision.value == "hold"
    assert result.next_context.failure_digest.reviewer_decisions == ("hold",)
    assert len(reader.strategy_versions()) == 1
    assert len(reader.trials()) == 1
    assert len(reader.trial_events(result.historical.trial_id)) == 2
    assert len(tuple((tmp_path / "strategies").glob("*/strategy.py"))) == 1
    assert len(tuple((tmp_path / "reviews").glob("*.json"))) == 1
