from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_intraday_registration import (
    GeneratedIntradayRegistrationError,
    register_generated_intraday_strategy,
)
from trading_agent.generated_intraday_research_models import (
    GeneratedIntradayResearchManifest,
    GeneratedStrategySelection,
)
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
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
REGISTERED_AT = dt.datetime(2026, 7, 23, 2, 32, tzinfo=dt.UTC)
SOURCE = (
    "def create_strategy(context):\n"
    "    class Strategy:\n"
    "        def observe(self, bar, candidate):\n"
    "            return None\n"
    "    return Strategy()\n"
)


def test_generated_manifest_registers_artifact_bound_version_idempotently(tmp_path: Path) -> None:
    # Given: a source-backed queue item and exact generated artifact lineage.
    ledger, queue_path, published = _evidence(tmp_path)
    selection = _selection(published)
    manifest = _manifest(selection, queue_path)

    # When: the same registration is applied twice.
    registration, created = register_generated_intraday_strategy(
        ledger,
        queue_path,
        manifest,
        selection,
        published,
    )
    replay, replay_created = register_generated_intraday_strategy(
        ledger,
        queue_path,
        manifest,
        selection,
        published,
    )

    # Then: one immutable generated-python version binds source, parameters, and lineage.
    assert created is True
    assert replay_created is False
    assert replay == registration
    assert registration.strategy_version == f"generated-python:{published.artifact.artifact_id}"
    assert registration.code_version == published.artifact.payload.source_sha256
    assert registration.parameter_set == published.artifact.payload.free_parameters
    assert len(ExperimentLedgerReader(ledger.path).strategy_versions()) == 1


def test_generated_registration_rejects_substituted_runtime_binding(tmp_path: Path) -> None:
    # Given: a valid queue and artifact but a selection with substituted runtime identity.
    ledger, queue_path, published = _evidence(tmp_path)
    selection = _selection(published).model_copy(update={"runtime_fingerprint": "f" * 64})
    manifest = _manifest(selection, queue_path)

    # When/Then: validation fails before a strategy version is appended.
    with pytest.raises(GeneratedIntradayRegistrationError):
        _ = register_generated_intraday_strategy(
            ledger,
            queue_path,
            manifest,
            selection,
            published,
        )
    assert ExperimentLedgerReader(ledger.path).strategy_versions() == ()


def test_generated_manifest_rejects_unbounded_or_under_costed_budget() -> None:
    # Given: a generated manifest whose empirical budget evades conservative bounds.
    payload = {
        "family": "generated_python_intraday_v1",
        "hypotheses": [],
        "source_queue_snapshot_id": "a" * 64,
        "input_sha256": "b" * 64,
        "registered_at": REGISTERED_AT,
        "minimum_training_sessions": 10,
        "max_bars": 100_001,
        "max_sessions": 10,
        "per_side_fee_bps": 1,
        "per_side_slippage_bps": 1,
        "bootstrap_samples": 100,
        "rss_limit_gib": 9.5,
    }

    # When/Then: the typed boundary rejects it.
    with pytest.raises(ValueError):
        _ = GeneratedIntradayResearchManifest.model_validate(payload)


def _evidence(
    tmp_path: Path,
) -> tuple[ExperimentLedgerStore, Path, PublishedGeneratedStrategy]:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_research_hypothesis_manifest(SOURCE_EXAMPLE, ledger)
    reader = ExperimentLedgerReader(ledger.path)
    queue = project_source_driven_hypothesis_queue(reader)
    queue_path, _ = publish_source_driven_hypothesis_queue(tmp_path / "queue", queue)
    card = reader.research_hypothesis_cards()[0].card
    source_manifest = load_research_hypothesis_manifest(SOURCE_EXAMPLE)
    proposal = ProposedHypothesis(
        card=card,
        cited_sources=source_manifest.research_sources,
        llm_receipt=LlmCallReceipt(
            "fixture-researcher-v1",
            "a" * 64,
            "b" * 64,
            7,
            0.0,
            dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
        ),
        strategy_draft=CandidateStrategyDraft(SOURCE, ("minimum_relative_volume",)),
    )
    store = GeneratedStrategyArtifactStore(
        tmp_path / "strategies",
        resolve_generated_strategy_runtime(Path(sys.executable)),
    )
    return ledger, queue_path, store.publish(proposal)


def _selection(published: PublishedGeneratedStrategy) -> GeneratedStrategySelection:
    artifact = published.artifact
    return GeneratedStrategySelection(
        artifact_id=artifact.artifact_id,
        hypothesis_id=artifact.payload.hypothesis_id,
        strategy_version=f"generated-python:{artifact.artifact_id}",
        queue_card_key=artifact.payload.card_key,
        data_foundation_sha256="c" * 64,
        runtime_fingerprint=artifact.payload.runtime.runtime_fingerprint,
        sandbox_profile_version=artifact.payload.runtime.sandbox_profile_version,
    )


def _manifest(selection: GeneratedStrategySelection, queue_path: Path) -> GeneratedIntradayResearchManifest:
    return GeneratedIntradayResearchManifest(
        hypotheses=(selection,),
        source_queue_snapshot_id=queue_path.stem.removeprefix("source_hypothesis_queue_"),
        input_sha256="d" * 64,
        registered_at=REGISTERED_AT,
        minimum_training_sessions=0,
        max_bars=10,
        max_sessions=1,
        per_side_fee_bps=5,
        per_side_slippage_bps=15,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )
