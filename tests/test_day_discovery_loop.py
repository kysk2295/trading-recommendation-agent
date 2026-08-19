from __future__ import annotations

import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from tests.day_strategy_capsule_support import no_signal_source, nondeterministic_source, proposal
from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.day_discovery_loop import (
    DayDiscoveryError,
    DayDiscoveryEvidenceView,
    DayDiscoveryLoop,
    DayDiscoveryLoopConfig,
    DayDiscoveryTriggerKind,
    ForwardProbeAdmissionRequest,
    _proposal_semantic_hash,
    sanitize_day_discovery_feedback,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.research_agent_cycle_models import ResearchAgentTriggerKind
from trading_agent.research_agent_service_runtime import day_discovery_market_runtime
from trading_agent.research_agent_source_adapters_primary import DaySourceAdapter
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import FixedHypothesisGenerator
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore

PROJECT = Path(__file__).resolve().parents[1]


def _proposal(source: str):
    value = proposal(source)
    return replace(
        value,
        llm_receipt=replace(value.llm_receipt, called_at=_view().observed_at),
        strategy_draft=replace(
            value.strategy_draft,
            methodology_tags=("novel_liquidity_echo", "online_state_machine"),
        ),
    )


def _pipeline(tmp_path: Path, generator, runtime) -> ResearcherPipeline:
    ledger = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    receipts = ResearcherReceiptStore(tmp_path / "receipts")
    return ResearcherPipeline(
        ResearcherPipelineServices(generator, DeterministicHypothesisCritic(max_free_parameters=4)),
        ResearcherPipelineStores(ledger, receipts, GeneratedStrategyArtifactStore(tmp_path / "artifacts", runtime)),
        ResearcherPipelineArtifacts(tmp_path / "manifests", tmp_path / "queue"),
    )


def _view(trigger: DayDiscoveryTriggerKind = DayDiscoveryTriggerKind.COMPLETED_BAR) -> DayDiscoveryEvidenceView:
    payload = json.loads((PROJECT / "tests/fixtures/day-research/discovery-evidence.json").read_text(encoding="utf-8"))
    payload["trigger_kind"] = trigger.value
    return DayDiscoveryEvidenceView.model_validate(payload)


@pytest.mark.parametrize("trigger", tuple(DayDiscoveryTriggerKind))
def test_all_five_discovery_triggers_are_admitted(trigger: DayDiscoveryTriggerKind) -> None:
    assert _view(trigger).trigger_kind is trigger


def test_naive_times_are_rejected_at_evidence_and_admission_boundaries() -> None:
    payload = _view().model_dump(mode="python")
    payload["observed_at"] = payload["observed_at"].replace(tzinfo=None)
    with pytest.raises(DayDiscoveryError, match="evidence_time_naive"):
        DayDiscoveryEvidenceView.model_validate(payload)

    with pytest.raises(DayDiscoveryError, match="forward_probe_time_naive"):
        ForwardProbeAdmissionRequest.canonical_id_for(
            {
                "capsule_id": "a" * 64,
                "market_id": MarketId.US_EQUITIES,
                "registration_completed_bar_at": dt.datetime(2026, 8, 20, 14, 30),
                "first_eligible_completed_bar_at": dt.datetime(2026, 8, 20, 14, 31),
                "trading_authority": False,
            }
        )


def test_safe_novel_non_enum_python_publishes_one_future_only_primary(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            pipeline=_pipeline(tmp_path, FixedHypothesisGenerator(_proposal(no_signal_source())), runtime),
            sandbox=GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            max_drafts=3,
        )
    ).run(_view())

    assert result.accepted is True
    assert result.drafts_attempted == 1
    assert result.capsule_id is not None
    assert result.admission_id is not None
    assert result.first_eligible_completed_bar_at > _view().completed_bar_at
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=MarketId.US_EQUITIES)[0].version
    assert "novel_liquidity_echo" in version.methodology_tags
    capsule = reader.day_strategy_capsules(MarketId.US_EQUITIES)[0].capsule
    reviewed = reader.day_attempts_for_review(MarketId.US_EQUITIES, version.hypothesis_version_id)[0]
    assert _view().completed_bar_at <= version.created_at
    assert version.created_at <= version.registration_completed_bar_at
    assert version.registration_completed_bar_at < result.first_eligible_completed_bar_at
    assert reviewed.attempt.started_at == _proposal(no_signal_source()).llm_receipt.called_at
    assert reviewed.attempt.finished_at is not None
    assert reviewed.attempt.finished_at < reviewed.binding.bound_at
    assert reviewed.binding.bound_at < capsule.published_at
    assert capsule.published_at < result.first_eligible_completed_bar_at


@pytest.mark.parametrize(
    ("reason", "source"),
    (
        ("semantic_duplicate", no_signal_source()),
        (
            "point_in_time_leakage",
            "def create_strategy(context):\n"
            "    class Strategy:\n"
            "        def observe(self, bar, candidate):\n"
            "            return bar['future_bar']\n"
            "    return Strategy()\n",
        ),
        ("compile_failed", "def create_strategy(:\n    pass\n"),
        ("unconstructible", "def create_strategy(context):\n    return object()\n"),
        (
            "sandbox_failed",
            "def create_strategy(context):\n"
            "    raise RuntimeError('blocked')\n"
            "    class Strategy:\n"
            "        def observe(self, bar, candidate):\n"
            "            return None\n"
            "    return Strategy()\n",
        ),
        ("nondeterministic", nondeterministic_source()),
    ),
)
def test_terminal_failures_are_visible_attempts_and_debit_budget(tmp_path: Path, reason: str, source: str) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    view = _view()
    if reason == "semantic_duplicate":
        view = view.model_copy(update={"existing_semantic_hashes": (_proposal_semantic_hash(_proposal(source)),)})
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            pipeline=_pipeline(tmp_path, FixedHypothesisGenerator(_proposal(source)), runtime),
            sandbox=GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            max_drafts=1,
        )
    ).run(view)

    assert result.accepted is False
    assert result.terminal_reason == reason
    assert result.drafts_attempted == 1
    assert result.remaining_budget == view.search_budget - 1
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version
    assert len(reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)) == 1


def test_feedback_allowlist_excludes_sealed_symbol_account_provider_and_secret() -> None:
    feedback = sanitize_day_discovery_feedback(
        {
            "family_id": "f" * 64,
            "hypothesis_version_id": "e" * 64,
            "outcome_class": "inconclusive",
            "bounded_metrics": {"signal_count": 2},
            "integrity_reason": "stale_data",
            "remaining_budget": 2,
            "next_review_date": "2026-08-21",
            "policy_priority": 1,
            "exact_sealed_holdout": 0.931,
            "symbol_contribution": {"AAPL": 0.9},
            "account_id": "secret-account",
            "provider": "broker",
            "api_key": "secret",
        }
    )
    encoded = feedback.model_dump_json()
    assert set(feedback.model_dump()) == {
        "family_id",
        "hypothesis_version_id",
        "outcome_class",
        "bounded_metrics",
        "integrity_reason",
        "data_reason",
        "runtime_reason",
        "novelty",
        "remaining_budget",
        "next_review_date",
        "policy_priority",
    }
    assert all(term not in encoded for term in ("AAPL", "secret-account", "broker", "0.931", "api_key"))


def test_market_cursors_and_failure_state_are_independent() -> None:
    us = _view()
    kr = us.model_copy(
        update={
            "market_id": MarketId.KR_EQUITIES,
            "universe_snapshot_id": "fixture-kr-universe",
            "cursor": "kr:7",
            "previous_failure": "kr_feed_stale",
        }
    )
    assert us.cursor != kr.cursor
    assert us.previous_failure is None
    assert kr.previous_failure == "kr_feed_stale"


@pytest.mark.parametrize(
    "unsafe_reason",
    ("provider_error", "account_missing", "secret_exposed", "UPPER_CASE", "bad reason"),
)
def test_previous_failure_rejects_unbounded_or_sensitive_text(unsafe_reason: str) -> None:
    payload = _view().model_dump(mode="python")
    payload["previous_failure"] = unsafe_reason
    with pytest.raises((DayDiscoveryError, ValueError), match="feedback_reason_invalid"):
        DayDiscoveryEvidenceView.model_validate(payload)


class _SequenceGenerator:
    def __init__(self, proposals):
        self.proposals = iter(proposals)
        self.calls = 0

    def propose(self, context):
        del context
        self.calls += 1
        return next(self.proposals)


def test_three_terminal_drafts_are_all_debited_and_no_more_are_generated(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    view = _view().model_copy(
        update={"existing_semantic_hashes": (_proposal_semantic_hash(_proposal(no_signal_source())),)}
    )
    generator = _SequenceGenerator([_proposal(no_signal_source())] * 4)
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            3,
        )
    ).run(view)
    assert result.drafts_attempted == 3
    assert result.remaining_budget == 0
    assert generator.calls == 3
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version
    assert len(reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)) == 3


def test_exhausted_prior_budget_does_not_call_model_or_create_hidden_attempt(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    view = _view().model_copy(update={"budget_debits_used": 3})
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            3,
        )
    ).run(view)
    assert result.terminal_reason == "budget_exhausted"
    assert result.drafts_attempted == 0
    assert generator.calls == 0
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_exact_cycle_replay_is_idempotent_and_creates_no_extra_rows(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    loop = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    first = loop.run(_view())
    replay = loop.run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=MarketId.US_EQUITIES)[0].version
    assert replay == first
    assert len(reader.day_attempts_for_review(MarketId.US_EQUITIES, version.hypothesis_version_id)) == 1
    assert len(reader.day_strategy_capsules(MarketId.US_EQUITIES)) == 1
    assert generator.calls == 1

    changed = _view().model_copy(update={"data_manifest_sha256": "e" * 64})
    with pytest.raises(DayDiscoveryError, match="cycle_evidence_identity_conflict"):
        loop.run(changed)


def test_concurrent_cycle_and_restarted_loop_replay_one_immutable_result(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    loop = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: loop.run(_view()), range(2)))
    assert results[0] == results[1]
    assert generator.calls == 1

    restarted_generator = _SequenceGenerator([])
    restarted = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, restarted_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    assert restarted.run(_view()) == results[0]
    assert restarted_generator.calls == 0


@pytest.mark.parametrize("tamper_kind", ("outer_id", "result_id", "noncanonical"))
def test_cycle_receipt_rejects_tampered_identity_and_noncanonical_bytes(tmp_path: Path, tamper_kind: str) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    loop = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    result = loop.run(_view())
    receipt_path = tmp_path / "manifests" / "day-discovery-cycle-receipts" / f"{result.cycle_id}.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tamper_kind == "outer_id":
        payload["cycle_id"] = "f" * 64
    elif tamper_kind == "result_id":
        payload["result"]["cycle_id"] = "e" * 64
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if tamper_kind == "noncanonical":
        encoded = f" {encoded}"
    receipt_path.write_text(encoded, encoding="utf-8")
    receipt_path.chmod(0o600)

    with pytest.raises(DayDiscoveryError, match="cycle_receipt"):
        loop.run(_view())
    assert generator.calls == 1


def test_missing_ai_methodology_is_terminal_and_market_failures_stay_isolated(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    raw = proposal(no_signal_source())
    raw = replace(raw, llm_receipt=replace(raw.llm_receipt, called_at=_view().observed_at))
    pipeline = _pipeline(tmp_path, FixedHypothesisGenerator(raw), runtime)
    loop = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            pipeline,
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    us = loop.run(_view())
    kr_view = _view().model_copy(
        update={
            "market_id": MarketId.KR_EQUITIES,
            "cursor": "kr:1",
            "universe_snapshot_id": "fixture-kr-universe",
        }
    )
    kr = loop.run(kr_view)
    reports = {item.market_id: item for item in day_discovery_market_runtime(pipeline.stores.ledger)}
    assert us.terminal_reason == "methodology_missing"
    assert kr.terminal_reason == "methodology_missing"
    assert reports[MarketId.US_EQUITIES].cursor != reports[MarketId.KR_EQUITIES].cursor
    assert reports[MarketId.US_EQUITIES].terminal_failure == "methodology_missing"
    assert reports[MarketId.KR_EQUITIES].terminal_failure == "methodology_missing"


def test_feedback_rejects_nested_or_unregistered_metrics() -> None:
    with pytest.raises((TypeError, ValueError)):
        sanitize_day_discovery_feedback(
            {
                "bounded_metrics": {"exact_holdout": 0.8, "signal_count": {"AAPL": 2}},
                "remaining_budget": 1,
            }
        )


def test_parameter_demand_above_remaining_budget_is_one_terminal_debit(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    candidate = replace(
        candidate,
        strategy_draft=replace(candidate.strategy_draft, free_parameters=("a", "b")),
    )
    generator = _SequenceGenerator([candidate, candidate])
    view = _view().model_copy(update={"search_budget": 1})
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            3,
        )
    ).run(view)
    assert result.terminal_reason == "budget_exhausted"
    assert result.drafts_attempted == 1
    assert result.remaining_budget == 0
    assert generator.calls == 1


@pytest.mark.parametrize("invalid_name", ("", "bad\nname"))
def test_structurally_returned_invalid_parameter_is_audited_once(tmp_path: Path, invalid_name: str) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    candidate = replace(
        candidate,
        strategy_draft=replace(candidate.strategy_draft, free_parameters=(invalid_name,)),
    )
    view = _view()
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, FixedHypothesisGenerator(candidate), runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)

    assert result.terminal_reason == "contract_invalid"
    assert result.drafts_attempted == 1
    assert result.remaining_budget == view.search_budget - 1
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version
    assert len(reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)) == 1


def test_structurally_returned_invalid_methodology_tag_is_terminally_audited_once(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    candidate = replace(
        candidate,
        strategy_draft=replace(candidate.strategy_draft, methodology_tags=("bad\ntag",)),
    )
    view = _view()
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, FixedHypothesisGenerator(candidate), runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)

    assert result.terminal_reason == "methodology_missing"
    assert result.drafts_attempted == 1
    assert result.remaining_budget == view.search_budget - 1
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version
    assert len(reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)) == 1


def test_missing_preregistered_falsification_is_a_visible_critic_rejection(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    invalid_hypothesis = candidate.card.hypothesis.model_copy(update={"falsification_rule": ""})
    candidate = replace(
        candidate,
        card=candidate.card.model_copy(update={"hypothesis": invalid_hypothesis}),
    )
    view = _view()
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, FixedHypothesisGenerator(candidate), runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)

    assert result.terminal_reason == "critic_rejected"
    assert result.drafts_attempted == 1
    assert result.remaining_budget == view.search_budget - 1


@pytest.mark.parametrize(
    ("trigger", "expected"),
    (
        (DayDiscoveryTriggerKind.COMPLETED_BAR, ResearchAgentTriggerKind.NEW_DATA),
        (DayDiscoveryTriggerKind.POINT_IN_TIME_EVIDENCE, ResearchAgentTriggerKind.NEW_DATA),
        (DayDiscoveryTriggerKind.TERMINAL_EVENT, ResearchAgentTriggerKind.EXPERIMENT_RESULT),
        (DayDiscoveryTriggerKind.REVIEW_CLOSE, ResearchAgentTriggerKind.REVIEWER_FEEDBACK),
        (DayDiscoveryTriggerKind.EXPLORATION_DUE, ResearchAgentTriggerKind.SCHEDULED_WAKE),
    ),
)
def test_day_source_adapter_ingests_each_canonical_discovery_trigger(
    tmp_path: Path,
    trigger: DayDiscoveryTriggerKind,
    expected: ResearchAgentTriggerKind,
) -> None:
    root = tmp_path / "private"
    session = root / "20260820"
    session.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    session.chmod(0o700)
    view = _view(trigger)
    artifact = session / "day-discovery-evidence.v1.json"
    artifact.write_text(canonical_model_json(view), encoding="utf-8")
    artifact.chmod(0o600)

    class _Paths:
        day_session_root = root
        market_context_root = tmp_path / "unused"
        kr_calendar_store: Path | None = None

    evidence = DaySourceAdapter().collect(_Paths(), view.observed_at)[0]
    assert evidence.trigger_kind is expected
    assert evidence.market_id == "us_equities"
    assert DayDiscoveryEvidenceView.model_validate_json(evidence.bounded_payload_json or "") == view
