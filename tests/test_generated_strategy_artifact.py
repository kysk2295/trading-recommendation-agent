from __future__ import annotations

import datetime as dt
import stat
import sys
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import HypothesisRegistration, ResearchHypothesisCard
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.research_hypothesis_registration import load_research_hypothesis_manifest
from trading_agent.researcher_agent import CandidateStrategyDraft, LlmCallReceipt, ProposedHypothesis

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_EXAMPLE = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"
STRATEGY_SOURCE = (
    "def create_strategy(context):\n"
    "    class Strategy:\n"
    "        def observe(self, bar, candidate):\n"
    "            return None\n"
    "    return Strategy()\n"
)


def test_strategy_artifact_publishes_and_replays_private_canonical_files(tmp_path: Path) -> None:
    # Given: a generated proposal and its exact bound Python runtime.
    store = GeneratedStrategyArtifactStore(
        tmp_path / "strategies",
        resolve_generated_strategy_runtime(Path(sys.executable)),
    )
    proposal = _proposal()

    # When: the same proposal is published and replayed.
    first = store.publish(proposal)
    replay = store.publish(proposal)
    loaded = store.load(first.artifact.artifact_id)

    # Then: identity, source, manifest, permissions, and replay outcome are canonical.
    assert replay.artifact == first.artifact
    assert first.created is True
    assert replay.created is False
    assert loaded == first.artifact
    assert first.source_path.read_text(encoding="utf-8") == STRATEGY_SOURCE
    assert stat.S_IMODE(first.source_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.manifest_path.stat().st_mode) == 0o600
    assert first.artifact.payload.response_sha256 == proposal.llm_receipt.response_sha256
    assert first.artifact.payload.runtime.runtime_fingerprint == store.runtime.runtime_fingerprint


def test_strategy_artifact_load_rejects_source_tampering(tmp_path: Path) -> None:
    # Given: a valid published generated strategy whose source bytes are later changed.
    store = GeneratedStrategyArtifactStore(
        tmp_path / "strategies",
        resolve_generated_strategy_runtime(Path(sys.executable)),
    )
    published = store.publish(_proposal())
    published.source_path.write_text("substituted", encoding="utf-8")
    published.source_path.chmod(0o600)

    # When/Then: the immutable load boundary rejects the substituted bytes.
    with pytest.raises(GeneratedStrategyArtifactError):
        _ = store.load(published.artifact.artifact_id)


def _proposal() -> ProposedHypothesis:
    manifest = load_research_hypothesis_manifest(SOURCE_EXAMPLE)
    scope = ExperimentScope.model_validate(manifest.experiment_scope.model_dump(mode="python"))
    registration = HypothesisRegistration(
        hypothesis_id=scope.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        primary_lane=scope.primary_lane,
        hypothesis=manifest.hypothesis,
        falsification_rule=manifest.falsification_rule,
        source_registered_at=scope.registered_at,
        ledger_recorded_at=scope.registered_at,
    )
    card = ResearchHypothesisCard(
        hypothesis=registration,
        research_source_keys=tuple(
            sorted(str(research_source_key(source)) for source in manifest.research_sources)
        ),
        economic_mechanism=manifest.economic_mechanism,
        counterfactual_baseline=manifest.counterfactual_baseline,
    )
    return ProposedHypothesis(
        card=card,
        cited_sources=manifest.research_sources,
        llm_receipt=LlmCallReceipt(
            model_id="fixture-researcher-v1",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            seed=7,
            temperature=0.0,
            called_at=dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC),
        ),
        strategy_draft=CandidateStrategyDraft(
            source_code=STRATEGY_SOURCE,
            free_parameters=("minimum_relative_volume",),
        ),
    )
