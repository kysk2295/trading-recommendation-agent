from __future__ import annotations

import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import trading_agent.day_discovery_loop as discovery_module
import trading_agent.day_discovery_state_machine as discovery_state_module
from tests.day_strategy_capsule_support import no_signal_source, nondeterministic_source, proposal
from trading_agent import researcher_llm
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
from trading_agent.lane_identity_models import LaneId
from trading_agent.research_agent_cycle_models import ResearchAgentTriggerKind
from trading_agent.research_agent_service_runtime import day_discovery_market_runtime
from trading_agent.research_agent_source_adapters_primary import DaySourceAdapter
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import FailureDigest, FixedHypothesisGenerator, ResearcherContext
from trading_agent.researcher_llm import FixtureLlmProposalClient, StructuredHypothesisGenerator
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


def test_discovery_view_requires_stable_budget_epoch_reference() -> None:
    assert _view().budget_epoch_ref == "us-equities-2026-08-20"


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


def test_completed_cycle_is_authoritatively_finalized_in_v11_ledger(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    pipeline = _pipeline(
        tmp_path,
        FixedHypothesisGenerator(_proposal(no_signal_source())),
        runtime,
    )

    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            pipeline=pipeline,
            sandbox=GeneratedStrategySandbox(
                runtime,
                tmp_path / "sandbox",
                _view().resource_limits,
            ),
            max_drafts=1,
        )
    ).run(_view())

    state = pipeline.stores.ledger.reader().day_discovery_cycle_state(result.cycle_id)
    assert state.events[-1].event_kind.value == "cycle_finalized"
    assert state.remaining_budget == result.remaining_budget


def test_empty_structured_response_is_recorded_before_terminal_parse_failure(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generator = StructuredHypothesisGenerator(
        FixtureLlmProposalClient(b""),
        ResearcherReceiptStore(tmp_path / "receipts"),
        lambda: _view().observed_at,
    )
    pipeline = _pipeline(tmp_path, generator, runtime)

    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            pipeline,
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())

    state = pipeline.stores.ledger.reader().day_discovery_cycle_state(result.cycle_id)
    assert result.terminal_reason == "model_response_malformed"
    assert state.events[2].event_kind.value == "call_response_recorded"
    assert state.debits[0].amount == 1


def test_budget_epoch_is_shared_across_distinct_cursor_cycles(tmp_path: Path) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    first_pipeline = _pipeline(
        tmp_path,
        FixedHypothesisGenerator(_proposal(no_signal_source())),
        runtime,
    )
    first = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            first_pipeline,
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())
    later_view = _view().model_copy(
        update={
            "cursor": "us:fixture:later",
            "observed_at": _view().observed_at + dt.timedelta(seconds=1),
        }
    )
    second = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(
                tmp_path,
                FixedHypothesisGenerator(_proposal(no_signal_source())),
                runtime,
            ),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", later_view.resource_limits),
            1,
        )
    ).run(later_view)

    assert first.remaining_budget == 2
    assert second.remaining_budget == 1


def test_finalized_first_cursor_replay_after_later_same_epoch_spend_is_idempotent(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    first_generator = _SequenceGenerator([_proposal(no_signal_source())])
    first = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, first_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())
    later_view = _view().model_copy(
        update={
            "cursor": "us:fixture:later",
            "observed_at": _view().observed_at + dt.timedelta(seconds=1),
        }
    )
    second_generator = _SequenceGenerator([_proposal(no_signal_source())])
    second = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, second_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", later_view.resource_limits),
            1,
        )
    ).run(later_view)
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    first_state = reader.day_discovery_cycle_state(first.cycle_id)

    assert first.remaining_budget == 2
    assert second.remaining_budget == 1
    assert first_state.remaining_budget == second.remaining_budget
    assert first.remaining_budget != first_state.remaining_budget

    replay_generator = _SequenceGenerator([])
    replay = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, replay_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())
    replayed_state = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader().day_discovery_cycle_state(
        first.cycle_id
    )

    assert replay == first
    assert first_generator.calls == 1
    assert second_generator.calls == 1
    assert replay_generator.calls == 0
    assert replayed_state.debits == first_state.debits
    assert replayed_state.events == first_state.events


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


class _PromptCapturingGenerator:
    def __init__(self, candidate):
        self.candidate = candidate
        self.prompts: list[str] = []

    def propose(self, context: ResearcherContext):
        self.prompts.append(researcher_llm._prompt(context))
        return self.candidate


def test_actual_day_prompt_projects_operational_identifiers_to_hashes_and_counts(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    marker = "api_key_super_secret_account_provider_token"
    view = _view().model_copy(
        update={
            "cursor": marker,
            "universe_snapshot_id": marker,
            "source_refs": (f"source:{marker}",),
            "evidence_schema": (marker,),
        }
    )
    generator = _PromptCapturingGenerator(_proposal(no_signal_source()))

    DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)
    prompt = json.loads(generator.prompts[0])
    day = prompt["day_discovery"]
    encoded = json.dumps(prompt, separators=(",", ":"), sort_keys=True)

    assert marker not in encoded
    assert day["source_ref_count"] == 1
    assert day["evidence_schema_count"] == 1
    assert len(day["cursor_sha256"]) == 64
    assert day["replay_bars"][0]["close"] == _view().replay_bars[0].close


@pytest.mark.parametrize(
    "sensitive_context",
    (
        "account_number_1234",
        "api_key_value",
        "authorization_bearer_value",
        "provider_id_value",
        "secret_token_value",
    ),
)
def test_sensitive_configured_context_is_rejected_before_prompt_or_model_call(
    tmp_path: Path,
    sensitive_context: str,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generator = _PromptCapturingGenerator(_proposal(no_signal_source()))
    context = ResearcherContext(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        sources=(),
        failure_digest=FailureDigest((), (), ()),
        regime_context=sensitive_context,
        existing_hypothesis_texts=(),
    )

    with pytest.raises(DayDiscoveryError, match="day_prompt_sensitive_context"):
        DayDiscoveryLoop(
            DayDiscoveryLoopConfig(
                _pipeline(tmp_path, generator, runtime),
                GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
                1,
            )
        ).run(_view(), context)
    assert generator.prompts == []


def test_terminal_drafts_exhaust_cartesian_budget_without_extra_generation(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    view = _view().model_copy(
        update={"existing_semantic_hashes": (_proposal_semantic_hash(_proposal(no_signal_source())),)}
    )
    candidates = [
        replace(
            _proposal(no_signal_source()),
            llm_receipt=replace(
                _proposal(no_signal_source()).llm_receipt,
                response_sha256=f"{index:064x}",
                called_at=_view().observed_at + dt.timedelta(microseconds=index),
            ),
        )
        for index in range(1, 5)
    ]
    generator = _SequenceGenerator(candidates)
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
    versions = reader.day_hypothesis_versions(market_id=view.market_id)
    assert (
        sum(
            len(reader.day_attempts_for_review(view.market_id, item.version.hypothesis_version_id)) for item in versions
        )
        == 3
    )


def test_untrusted_view_debit_counter_does_not_override_authoritative_ledger(tmp_path: Path) -> None:
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
    assert result.accepted is True
    assert result.remaining_budget == 2
    assert generator.calls == 1
    assert (tmp_path / "ledger.sqlite3").is_file()


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


def test_restart_resumes_prepared_branch_after_final_receipt_crash_without_second_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    first_generator = _SequenceGenerator([_proposal(no_signal_source())])
    first_loop = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, first_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    real_publish = discovery_state_module.publish_private_immutable_text

    def crash_before_final(path: Path, payload: str) -> bool:
        if path.name.endswith(".json") and ".prepared." not in path.name:
            raise discovery_module.InvalidPrivateImmutableFileError
        return real_publish(path, payload)

    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", crash_before_final)
    with pytest.raises(DayDiscoveryError, match="cycle_receipt_publication_failed"):
        first_loop.run(_view())
    assert first_generator.calls == 1
    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", real_publish)

    restarted_generator = _SequenceGenerator([])
    recovered = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, restarted_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=_view().market_id)[0].version

    assert recovered.accepted is True
    assert restarted_generator.calls == 0
    assert len(reader.day_hypothesis_versions(market_id=_view().market_id)) == 1
    assert len(reader.day_attempts_for_review(_view().market_id, version.hypothesis_version_id)) == 1
    assert len(reader.day_strategy_capsules(_view().market_id)) == 1


def test_restart_after_artifact_intent_terminalizes_unknown_without_double_debit(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))

    def crash_after_version(phase: str) -> None:
        if phase == "resolution_intent":
            raise RuntimeError("simulated_crash")

    first_generator = _SequenceGenerator([_proposal(no_signal_source())])
    with pytest.raises(RuntimeError, match="simulated_crash"):
        DayDiscoveryLoop(
            DayDiscoveryLoopConfig(
                _pipeline(tmp_path, first_generator, runtime),
                GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
                1,
                fault_injector=crash_after_version,
            )
        ).run(_view())

    restarted_generator = _SequenceGenerator([])
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, restarted_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=_view().market_id)[0].version
    reviewed = reader.day_attempts_for_review(_view().market_id, version.hypothesis_version_id)

    assert result.accepted is False
    assert result.terminal_reason == "artifact_outcome_unknown"
    assert first_generator.calls == 1
    assert restarted_generator.calls == 0
    assert len(reviewed) == 1
    assert reviewed[0].binding.search_budget_debit == 1


def test_restart_regenerates_only_final_projection_without_branch_journals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    first_generator = _SequenceGenerator([_proposal(no_signal_source())])
    loop = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, first_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )
    real_publish = discovery_state_module.publish_private_immutable_text

    def crash_before_final(path: Path, payload: str) -> bool:
        if path.name.endswith(".json") and ".prepared." not in path.name:
            raise discovery_module.InvalidPrivateImmutableFileError
        return real_publish(path, payload)

    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", crash_before_final)
    with pytest.raises(DayDiscoveryError, match="cycle_receipt_publication_failed"):
        loop.run(_view())
    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", real_publish)
    root = tmp_path / "manifests" / "day-discovery-cycle-receipts"
    assert tuple(root.glob("*.prepared.*.json")) == ()
    restarted_generator = _SequenceGenerator([])
    recovered = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, restarted_generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    ).run(_view())
    assert recovered.accepted is True
    assert first_generator.calls == 1
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


def test_cycle_receipt_binds_ledger_head_event_id_and_reconstructs_from_sqlite(
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
    result = loop.run(_view())
    state = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader().day_discovery_cycle_state(
        result.cycle_id
    )
    final = state.events[-1]
    receipt_path = tmp_path / "manifests" / "day-discovery-cycle-receipts" / f"{result.cycle_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt["ledger_head_event_id"] == final.event_id
    assert json.loads(final.payload_json).get("ledger_head_event_id") != final.event_id

    receipt_path.unlink()
    replayed = loop.run(_view())
    republished = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert replayed == result
    assert republished["ledger_head_event_id"] == final.event_id
    assert generator.calls == 1


def test_cycle_receipt_rejects_swapped_ledger_head_event_id(tmp_path: Path) -> None:
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
    state = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader().day_discovery_cycle_state(
        result.cycle_id
    )
    receipt_path = tmp_path / "manifests" / "day-discovery-cycle-receipts" / f"{result.cycle_id}.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["ledger_head_event_id"] = state.events[0].event_id
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
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


@pytest.mark.parametrize(
    ("search_budget", "accepted", "terminal_reason", "expected_combinations", "expected_debit"),
    (
        (8, True, None, 8, 8),
        (7, False, "budget_exhausted", 7, 1),
    ),
)
def test_cartesian_parameter_combinations_control_admission_and_debit(
    tmp_path: Path,
    search_budget: int,
    accepted: bool,
    terminal_reason: str | None,
    expected_combinations: int,
    expected_debit: int,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    candidate = replace(
        candidate,
        strategy_draft=replace(candidate.strategy_draft, free_parameters=("a", "b", "c")),
    )
    view = _view().model_copy(update={"search_budget": search_budget})
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, FixedHypothesisGenerator(candidate), runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version
    reviewed = reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)
    preregistration = next(
        item
        for item in reader.strategy_research_preregistrations()
        if item.hypothesis.hypothesis_id == version.hypothesis_version_id
    )

    assert result.accepted is accepted
    assert result.terminal_reason == terminal_reason
    assert result.remaining_budget == search_budget - expected_debit
    assert tuple(len(parameter.values) for parameter in version.free_parameters) == (2, 2, 2)
    assert version.search_budget.max_parameter_combinations == expected_combinations
    assert preregistration.hypothesis.search_budget.max_parameter_combinations == expected_combinations
    assert reviewed[0].binding.search_budget_debit == expected_debit


def test_thirteen_free_parameter_names_are_terminally_audited_with_bounded_debit(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    candidate = replace(
        candidate,
        strategy_draft=replace(
            candidate.strategy_draft,
            free_parameters=tuple(f"parameter_{index}" for index in range(13)),
        ),
    )
    view = _view()
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, FixedHypothesisGenerator(candidate), runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version
    reviewed = reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)
    preregistration = next(
        item
        for item in reader.strategy_research_preregistrations()
        if item.hypothesis.hypothesis_id == version.hypothesis_version_id
    )

    assert result.accepted is False
    assert result.drafts_attempted == 1
    assert result.remaining_budget == view.search_budget - 2
    assert len(reviewed) == 1
    assert tuple(parameter.name for parameter in version.free_parameters) == ("invalid_ai_parameter_declaration",)
    assert version.search_budget.max_parameter_combinations == 2
    assert preregistration.hypothesis.search_budget.max_parameter_combinations == 2
    assert reviewed[0].binding.search_budget_debit == 2


def test_proposal_after_first_eligible_bar_is_terminally_audited(
    tmp_path: Path,
) -> None:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    candidate = _proposal(no_signal_source())
    candidate = replace(
        candidate,
        llm_receipt=replace(
            candidate.llm_receipt,
            called_at=_view().first_eligible_completed_bar_at + dt.timedelta(minutes=1),
        ),
    )
    view = _view()
    result = DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, FixedHypothesisGenerator(candidate), runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", view.resource_limits),
            1,
        )
    ).run(view)
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=view.market_id)[0].version

    assert result.accepted is False
    assert result.terminal_reason == "forward_probe_not_future_only"
    assert len(reader.day_attempts_for_review(view.market_id, version.hypothesis_version_id)) == 1


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
    assert result.remaining_budget == view.search_budget - 2
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

    assert result.terminal_reason == "model_response_malformed"
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
