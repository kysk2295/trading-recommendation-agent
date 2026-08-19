from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.strategy_research_contract_fixtures import SHA_A, hypothesis, source
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_ledger import ExactHoldoutMetric, HoldoutReveal
from trading_agent.strategy_research_models import EvidenceRef, PreregistrationManifest
from trading_agent.strategy_research_results import TerminalResearchResult
from trading_agent.strategy_research_runtime_state import slot_from_event
from trading_agent.strategy_research_shadow import (
    FutureShadowObservation,
    FutureShadowPolicy,
    StrategyResearchShadowError,
    append_future_shadow_observation,
)
from trading_agent.strategy_research_types import (
    EvidenceKind,
    SafeTerminalReason,
    TerminalOutcome,
)

UTC = dt.UTC
TERMINAL_AT = dt.datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def _supported_store(path: Path, *, outcome: TerminalOutcome = TerminalOutcome.SUPPORTED) -> ExperimentLedgerStore:
    store = ExperimentLedgerStore(path)
    manifest = PreregistrationManifest.from_hypothesis(
        hypothesis(), preregistered_at=hypothesis().created_at + dt.timedelta(minutes=1)
    )
    result = TerminalResearchResult(
        result_id="safe-result-1",
        hypothesis_id=manifest.hypothesis.hypothesis_id,
        owner_agent_id=manifest.hypothesis.agent_id,
        outcome=outcome,
        reason_codes=(SafeTerminalReason.PREREGISTERED_SUPPORT_MET,),
        artifact_refs=(f"artifact://safe/{SHA_A}",),
        evaluated_at=TERMINAL_AT,
    )
    sealed = manifest.hypothesis.holdout_period_sealed_ref
    reveal = HoldoutReveal(
        reveal_id="reveal-1",
        hypothesis_id=manifest.hypothesis.hypothesis_id,
        seal_id=sealed.seal_id,
        commitment_sha256=sealed.commitment_sha256,
        reviewer_id="reviewer",
        exact_metrics=(ExactHoldoutMetric(name="private", value=1.0, lower=0.0, upper=2.0),),
        sanitized_result=result,
        revealed_at=TERMINAL_AT,
    )
    with store.writer() as writer:
        assert writer.register_strategy_research(manifest)
        assert writer.reveal_strategy_research_holdout(reveal)
    return store


def _future_ref(*, kind: EvidenceKind = EvidenceKind.REAL) -> EvidenceRef:
    original = source(kind=kind)
    return original.model_copy(
        update={
            "evidence_id": f"future-{kind.value}",
            "source_id": f"future-source-{kind.value}",
            "as_of": TERMINAL_AT + dt.timedelta(minutes=1),
            "available_at": TERMINAL_AT + dt.timedelta(minutes=2),
        }
    )


def _observation(*, kind: EvidenceKind = EvidenceKind.REAL) -> FutureShadowObservation:
    manifest = hypothesis()
    return FutureShadowObservation(
        observation_id="shadow-observation-1",
        result_id="safe-result-1",
        hypothesis_id=manifest.hypothesis_id,
        owner_agent_id=manifest.agent_id,
        observed_at=TERMINAL_AT + dt.timedelta(minutes=3),
        source_refs=(_future_ref(kind=kind),),
        sample_count=40,
        ci_width=0.015,
    )


def test_supported_result_appends_future_shadow_progress_and_candidate_state(tmp_path: Path) -> None:
    # Given: a sanitized supported result and strictly future real evidence.
    store = _supported_store(tmp_path / "ledger.sqlite3")
    policy = FutureShadowPolicy(future_sample_target=40, maximum_ci_width=0.02)

    # When: the first forward observation meets the preregistered information policy.
    event, created = append_future_shadow_observation(store, _observation(), policy)

    # Then: one append-only candidate-awaiting-owner state exists with no authority.
    history = ExperimentLedgerReader(store.path).strategy_research_agent_state(event.agent_id)
    assert created is True
    assert history == (event,)
    assert event.state == "paper_candidate"
    assert event.shadow_sample_count == 40
    assert event.shadow_information_sufficient is True
    assert event.owner_approval_required is True
    assert event.trading_authority is event.profitability_claim is False
    assert slot_from_event(event).state == "paper_candidate"


@pytest.mark.parametrize("outcome", [TerminalOutcome.REFUTED, TerminalOutcome.INCONCLUSIVE])
def test_non_supported_result_cannot_open_shadow(tmp_path: Path, outcome: TerminalOutcome) -> None:
    # Given: a sanitized terminal result that is not supported.
    store = _supported_store(tmp_path / "ledger.sqlite3", outcome=outcome)

    # When / Then: shadow admission fails before state mutation.
    with pytest.raises(StrategyResearchShadowError, match="shadow_requires_supported_result"):
        append_future_shadow_observation(store, _observation(), FutureShadowPolicy())
    assert ExperimentLedgerReader(store.path).strategy_research_agent_state(hypothesis().agent_id) == ()


def test_backdated_or_replay_shadow_evidence_fails_closed(tmp_path: Path) -> None:
    # Given: supported state but either terminal-time or replay evidence.
    store = _supported_store(tmp_path / "ledger.sqlite3")
    backdated_refs = tuple(
        item.model_copy(update={"as_of": TERMINAL_AT, "available_at": TERMINAL_AT})
        for item in _observation().source_refs
    )
    backdated = _observation().model_copy(update={"observed_at": TERMINAL_AT, "source_refs": backdated_refs})
    replay = _observation(kind=EvidenceKind.REPLAY)

    # When / Then: both unsafe sources are rejected without an append.
    with pytest.raises(StrategyResearchShadowError, match="shadow_time_not_future"):
        append_future_shadow_observation(store, backdated, FutureShadowPolicy())
    with pytest.raises(StrategyResearchShadowError, match="shadow_replay_forbidden"):
        append_future_shadow_observation(store, replay, FutureShadowPolicy())
    assert ExperimentLedgerReader(store.path).strategy_research_agent_state(hypothesis().agent_id) == ()


def test_shadow_replay_is_idempotent_and_same_id_payload_conflicts(tmp_path: Path) -> None:
    # Given: one accepted immutable shadow observation.
    store = _supported_store(tmp_path / "ledger.sqlite3")
    observation = _observation()
    policy = FutureShadowPolicy()
    first, first_created = append_future_shadow_observation(store, observation, policy)

    # When: the byte-identical observation replays, then the same ID changes.
    replay, replay_created = append_future_shadow_observation(store, observation, policy)
    changed = observation.model_copy(update={"sample_count": observation.sample_count + 1})

    # Then: replay is stable and changed identity conflicts.
    assert (first_created, replay_created, replay) == (True, False, first)
    with pytest.raises(StrategyResearchShadowError, match="shadow_observation_conflict"):
        append_future_shadow_observation(store, changed, policy)
    assert len(ExperimentLedgerReader(store.path).strategy_research_agent_state(first.agent_id)) == 1


def test_shadow_information_policy_cannot_weaken_preregistered_gate(tmp_path: Path) -> None:
    # Given: a supported result and a caller-supplied policy below the persisted sample target.
    store = _supported_store(tmp_path / "ledger.sqlite3")
    weakened = FutureShadowPolicy(future_sample_target=1, maximum_ci_width=0.02)

    # When / Then: the persisted hypothesis policy wins and no shadow event is appended.
    with pytest.raises(StrategyResearchShadowError, match="shadow_policy_mismatch"):
        append_future_shadow_observation(store, _observation(), weakened)
    assert ExperimentLedgerReader(store.path).strategy_research_agent_state(hypothesis().agent_id) == ()
