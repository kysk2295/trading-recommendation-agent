from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_intraday_loop import (
    GeneratedIntradayLoopPaths,
    run_generated_intraday_loop,
)
from trading_agent.generated_intraday_research_models import (
    GeneratedIntradayResearchManifest,
    GeneratedStrategySelection,
)
from trading_agent.generated_intraday_trial import GeneratedIntradayTrialError
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategyLimits, GeneratedStrategySandbox
from trading_agent.intraday_research_artifacts import load_intraday_experiment_artifact
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.research_hypothesis_registration import (
    load_research_hypothesis_manifest,
    register_research_hypothesis_manifest,
)
from trading_agent.researcher_agent import CandidateStrategyDraft, LlmCallReceipt, ProposedHypothesis
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_EXAMPLE = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"
INPUT_CSV = PROJECT / "examples" / "example_intraday.csv"
INPUT_SHA256 = "2a0222a20540d7d07b95130dc6a7414733f75f5210958820fde8021259e96391"
FOUNDATION = PROJECT / "examples" / "data" / "us-vwap-reclaim-historical-fixture-v1.json"
FOUNDATION_SHA256 = "baccd5b6944d239d4467267b98ab24790a78b20fb68f9d337d0cc1465e276e94"
REGISTERED_AT = dt.datetime(2026, 7, 23, 2, 32, tzinfo=dt.UTC)
SIGNAL_SOURCE = (
    "def create_strategy(context):\n"
    "    class Strategy:\n"
    "        emitted = False\n"
    "        def observe(self, bar, candidate):\n"
    "            if self.emitted:\n"
    "                return None\n"
    "            self.emitted = True\n"
    "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
    "'entry': bar['close'], 'stop': bar['low'], 'rationale': 'generated fixture'}\n"
    "    return Strategy()\n"
)


def test_generated_loop_completes_real_sandbox_trial_and_existing_review(tmp_path: Path) -> None:
    # Given: one generated artifact, source queue, completed bars, and ready data foundation.
    setup = _setup(tmp_path, SIGNAL_SOURCE)

    # When: the bounded generated research loop runs and then replays exactly.
    first = run_generated_intraday_loop(*setup.arguments)
    replay = run_generated_intraday_loop(*setup.arguments)

    # Then: one schema-v3 experiment and HOLD review are immutable with no repeated creation.
    assert first.decision is IntradayReviewerDecision.HOLD
    assert first.experiment_created is True
    assert first.review_created is True
    assert replay.experiment_created is False
    assert replay.review_created is False
    experiment = load_intraday_experiment_artifact(
        tmp_path / "experiments" / f"intraday_walk_forward_{first.experiment_artifact_id}.json"
    )
    assert experiment.schema_version == 3
    assert experiment.payload.result.side_cost_bps == 20
    assert experiment.payload.result.trade_count == 1
    events = ExperimentLedgerReader(setup.ledger.path).trial_events(first.trial_id)
    assert tuple(event.event.event_kind.value for event in events) == ("started", "completed")


def test_generated_loop_records_timeout_as_failed_terminal_event(tmp_path: Path) -> None:
    # Given: a registered generated strategy that never returns from observe.
    source = (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            while True:\n"
        "                pass\n"
        "    return Strategy()\n"
    )
    setup = _setup(tmp_path, source, wall_seconds=0.2)

    # When/Then: evaluation fails but STARTED and exact failure reason remain in the ledger.
    with pytest.raises(GeneratedIntradayTrialError, match="frame_timeout"):
        _ = run_generated_intraday_loop(*setup.arguments)
    trials = ExperimentLedgerReader(setup.ledger.path).trials()
    assert len(trials) == 1
    events = ExperimentLedgerReader(setup.ledger.path).trial_events(trials[0].registration.trial_id)
    assert tuple(event.event.event_kind.value for event in events) == ("started", "failed")
    assert events[-1].event.reason_codes == ("frame_timeout",)


class _Setup:
    def __init__(
        self,
        ledger: ExperimentLedgerStore,
        manifest: GeneratedIntradayResearchManifest,
        selection: GeneratedStrategySelection,
        published: PublishedGeneratedStrategy,
        sandbox: GeneratedStrategySandbox,
        paths: GeneratedIntradayLoopPaths,
    ) -> None:
        self.ledger = ledger
        self.arguments = (manifest, selection, published, sandbox, paths)


def _setup(tmp_path: Path, source: str, *, wall_seconds: float = 2.0) -> _Setup:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_research_hypothesis_manifest(SOURCE_EXAMPLE, ledger)
    reader = ExperimentLedgerReader(ledger.path)
    queue = project_source_driven_hypothesis_queue(reader)
    queue_path, _ = publish_source_driven_hypothesis_queue(tmp_path / "queue", queue)
    source_manifest = load_research_hypothesis_manifest(SOURCE_EXAMPLE)
    proposal = ProposedHypothesis(
        card=reader.research_hypothesis_cards()[0].card,
        cited_sources=source_manifest.research_sources,
        llm_receipt=LlmCallReceipt(
            "fixture-researcher-v1",
            "a" * 64,
            "b" * 64,
            7,
            0.0,
            dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
        ),
        strategy_draft=CandidateStrategyDraft(source, ("minimum_relative_volume",)),
    )
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    published = GeneratedStrategyArtifactStore(tmp_path / "strategies", runtime).publish(proposal)
    artifact = published.artifact
    selection = GeneratedStrategySelection(
        artifact_id=artifact.artifact_id,
        hypothesis_id=artifact.payload.hypothesis_id,
        strategy_version=f"generated-python:{artifact.artifact_id}",
        queue_card_key=artifact.payload.card_key,
        data_foundation_sha256=FOUNDATION_SHA256,
        runtime_fingerprint=runtime.runtime_fingerprint,
        sandbox_profile_version=runtime.sandbox_profile_version,
    )
    manifest = GeneratedIntradayResearchManifest(
        hypotheses=(selection,),
        source_queue_snapshot_id=queue.snapshot_id,
        input_sha256=INPUT_SHA256,
        registered_at=REGISTERED_AT,
        minimum_training_sessions=0,
        max_bars=10,
        max_sessions=1,
        per_side_fee_bps=5,
        per_side_slippage_bps=15,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )
    sandbox = GeneratedStrategySandbox(
        runtime,
        tmp_path / "tasks",
        GeneratedStrategyLimits(wall_seconds=wall_seconds),
    )
    paths = GeneratedIntradayLoopPaths(
        input_csv=INPUT_CSV,
        experiment_ledger=ledger.path,
        experiment_root=tmp_path / "experiments",
        review_root=tmp_path / "reviews",
        source_queue_artifact=queue_path,
        data_foundation_manifest=FOUNDATION,
    )
    return _Setup(ledger, manifest, selection, published, sandbox, paths)
