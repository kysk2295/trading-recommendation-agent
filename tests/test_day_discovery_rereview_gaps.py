from __future__ import annotations

import base64
import datetime as dt
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import trading_agent.day_discovery_loop as discovery_module
import trading_agent.day_discovery_state_machine as discovery_state_module
from tests.day_strategy_capsule_support import no_signal_source
from tests.test_day_discovery_loop import _pipeline, _proposal, _SequenceGenerator, _view
from trading_agent.day_discovery_loop import (
    DayDiscoveryError,
    DayDiscoveryLoop,
    DayDiscoveryLoopConfig,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.lane_identity_models import LaneId
from trading_agent.researcher_agent import FailureDigest, ResearcherContext


def _loop(tmp_path: Path, generator: _SequenceGenerator) -> DayDiscoveryLoop:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    return DayDiscoveryLoop(
        DayDiscoveryLoopConfig(
            _pipeline(tmp_path, generator, runtime),
            GeneratedStrategySandbox(runtime, tmp_path / "sandbox", _view().resource_limits),
            1,
        )
    )


def test_orphaned_pre_call_reservation_terminalizes_without_second_model_call(
    tmp_path: Path,
) -> None:
    first_generator = _SequenceGenerator([_proposal(no_signal_source())])
    configured = _loop(tmp_path, first_generator)

    def crash_after_reservation(phase: str) -> None:
        if phase == "call_reserved":
            raise RuntimeError("simulated_crash")

    first = DayDiscoveryLoop(
        replace(configured.config, fault_injector=crash_after_reservation)
    )
    with pytest.raises(RuntimeError, match="simulated_crash"):
        first.run(_view())

    restarted_generator = _SequenceGenerator([])
    result = _loop(tmp_path, restarted_generator).run(_view())

    assert result.terminal_reason == "model_call_outcome_unknown"
    assert result.drafts_attempted == 1
    assert result.remaining_budget == _view().search_budget - 1
    assert first_generator.calls == 0
    assert restarted_generator.calls == 0
    assert (tmp_path / "ledger.sqlite3").is_file()
    assert _loop(tmp_path, _SequenceGenerator([])).run(_view()) == result


def test_recorded_artifact_failure_replays_exact_terminal_after_final_receipt_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_generator = _SequenceGenerator([_proposal(no_signal_source())])
    first = _loop(tmp_path, first_generator)
    real_artifact_publish = GeneratedStrategyArtifactStore.publish
    real_receipt_publish = discovery_state_module.publish_private_immutable_text

    def fail_artifact(self: GeneratedStrategyArtifactStore, proposal: object) -> object:
        del self, proposal
        raise GeneratedStrategyArtifactError("simulated_failure")

    def fail_final_receipt(path: Path, payload: str) -> bool:
        if path.name.endswith(".json") and all(
            marker not in path.name
            for marker in (".prepared.", ".reservation.", ".resolution.")
        ):
            raise discovery_module.InvalidPrivateImmutableFileError
        return real_receipt_publish(path, payload)

    monkeypatch.setattr(GeneratedStrategyArtifactStore, "publish", fail_artifact)
    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", fail_final_receipt)
    with pytest.raises(DayDiscoveryError, match="cycle_receipt_publication_failed"):
        first.run(_view())
    monkeypatch.setattr(GeneratedStrategyArtifactStore, "publish", real_artifact_publish)
    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", real_receipt_publish)

    restarted_generator = _SequenceGenerator([])
    result = _loop(tmp_path, restarted_generator).run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=_view().market_id)[0].version

    assert result.terminal_reason == "artifact_publication_failed"
    assert result.accepted is False
    assert first_generator.calls == 1
    assert restarted_generator.calls == 0
    assert len(reader.day_attempts_for_review(_view().market_id, version.hypothesis_version_id)) == 1
    assert reader.day_strategy_capsules(_view().market_id) == ()


@pytest.mark.parametrize(
    "tamper",
    ("attempt_id", "debit", "attempt_started_at", "terminal_reason"),
)
def test_canonical_prepared_forgery_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    loop = _loop(tmp_path, generator)
    real_receipt_publish = discovery_state_module.publish_private_immutable_text

    def fail_final_receipt(path: Path, payload: str) -> bool:
        if path.name.endswith(".json") and all(
            marker not in path.name
            for marker in (".prepared.", ".reservation.", ".resolution.")
        ):
            raise discovery_module.InvalidPrivateImmutableFileError
        return real_receipt_publish(path, payload)

    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", fail_final_receipt)
    with pytest.raises(DayDiscoveryError, match="cycle_receipt_publication_failed"):
        loop.run(_view())
    monkeypatch.setattr(discovery_state_module, "publish_private_immutable_text", real_receipt_publish)
    database = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(database) as connection:
        cycle_id = connection.execute(
            "SELECT cycle_id FROM day_discovery_cycles"
        ).fetchone()[0]
        row = connection.execute(
            "SELECT event_id,payload_json FROM day_discovery_events "
            "WHERE event_kind='branch_prepared'"
        ).fetchone()
        assert row is not None
        outer = json.loads(row[1])
        payload = json.loads(outer["payload_json"])
        prepared = payload["prepared"]
        if tamper == "attempt_id":
            prepared["attempt_id"] = "f" * 64
        elif tamper == "debit":
            prepared["search_budget_debit"] = 2
        elif tamper == "attempt_started_at":
            prepared["attempt_started_at"] = (
                dt.datetime.fromisoformat(prepared["attempt_started_at"])
                + dt.timedelta(seconds=1)
            ).isoformat()
        else:
            prepared["terminal_reason"] = "forged_reason"
            prepared["proposal_card"] = None
        outer["payload_json"] = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        connection.execute("DROP TRIGGER day_discovery_events_no_update")
        connection.execute(
            "UPDATE day_discovery_events SET payload_json=? WHERE event_id=?",
            (
                json.dumps(outer, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                row[0],
            ),
        )
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        ExperimentLedgerStore(database).reader().day_discovery_cycle_state(cycle_id)


@pytest.mark.parametrize(
    "tamper",
    ("negative_remaining", "accepted_without_artifacts", "draft_mismatch", "forged_reason"),
)
def test_canonical_contradictory_cycle_receipt_is_rejected(
    tmp_path: Path,
    tamper: str,
) -> None:
    loop = _loop(tmp_path, _SequenceGenerator([_proposal(no_signal_source())]))
    result = loop.run(_view())
    receipt_path = (
        tmp_path
        / "manifests"
        / "day-discovery-cycle-receipts"
        / f"{result.cycle_id}.json"
    )
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    branch = payload["result"]
    if tamper == "negative_remaining":
        branch["remaining_budget"] = -1
    elif tamper == "accepted_without_artifacts":
        branch["capsule_id"] = None
    elif tamper == "draft_mismatch":
        branch["drafts_attempted"] = 2
    else:
        branch.update(
            {
                "accepted": False,
                "capsule_id": None,
                "admission_id": None,
                "terminal_reason": "forged_reason",
            }
        )
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    receipt_path.chmod(0o600)

    with pytest.raises(DayDiscoveryError, match="cycle_receipt"):
        loop.run(_view())


def test_zero_declared_parameters_use_product_identity_one(tmp_path: Path) -> None:
    result = _loop(
        tmp_path,
        _SequenceGenerator([_proposal(no_signal_source())]),
    ).run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=_view().market_id)[0].version
    preregistration = next(
        item
        for item in reader.strategy_research_preregistrations()
        if item.hypothesis.hypothesis_id == version.hypothesis_version_id
    )
    reviewed = reader.day_attempts_for_review(_view().market_id, version.hypothesis_version_id)

    assert result.remaining_budget == _view().search_budget - 1
    assert version.free_parameters == ()
    assert version.search_budget.max_parameter_combinations == 1
    assert preregistration.hypothesis.free_parameters == ()
    assert preregistration.hypothesis.search_budget.max_parameter_combinations == 1
    assert reviewed[0].binding.search_budget_debit == 1


@pytest.mark.parametrize(
    "sensitive",
    (
        "api.key=abc123",
        "api key abc123",
        "api%5Fkey%3Dabc123",
        base64.b64encode(b"authorization: Bearer abc123").decode(),
    ),
)
def test_normalized_sensitive_context_never_reaches_model(
    tmp_path: Path,
    sensitive: str,
) -> None:
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    context = ResearcherContext(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        sources=(),
        failure_digest=FailureDigest((), (), ()),
        regime_context=sensitive,
        existing_hypothesis_texts=(),
    )

    with pytest.raises(DayDiscoveryError, match="day_prompt_sensitive_context"):
        _loop(tmp_path, generator).run(_view(), context)
    assert generator.calls == 0


def test_legitimate_liquidity_provider_context_reaches_model(tmp_path: Path) -> None:
    generator = _SequenceGenerator([_proposal(no_signal_source())])
    context = ResearcherContext(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        sources=(),
        failure_digest=FailureDigest((), (), ()),
        regime_context="liquidity provider concentration is elevated",
        existing_hypothesis_texts=(),
    )

    result = _loop(tmp_path, generator).run(_view(), context)

    assert result.accepted is True
    assert generator.calls == 1


def test_late_proposal_is_terminal_without_backdated_ledger_times(tmp_path: Path) -> None:
    called_at = _view().first_eligible_completed_bar_at + dt.timedelta(minutes=1)
    candidate = replace(
        _proposal(no_signal_source()),
        llm_receipt=replace(_proposal(no_signal_source()).llm_receipt, called_at=called_at),
    )
    result = _loop(tmp_path, _SequenceGenerator([candidate])).run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    version = reader.day_hypothesis_versions(market_id=_view().market_id)[0].version
    reviewed = reader.day_attempts_for_review(_view().market_id, version.hypothesis_version_id)[0]
    preregistration = next(
        item
        for item in reader.strategy_research_preregistrations()
        if item.hypothesis.hypothesis_id == version.hypothesis_version_id
    )

    assert result.terminal_reason == "forward_probe_not_future_only"
    assert version.created_at >= called_at
    assert version.registration_completed_bar_at >= called_at
    assert version.first_shadow_eligible_at > called_at
    assert preregistration.preregistered_at >= called_at
    assert reviewed.attempt.started_at == called_at
    assert reviewed.attempt.finished_at is not None
    assert reviewed.attempt.finished_at >= called_at
    assert reviewed.binding.bound_at > called_at


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("hypothesis", " leading hypothesis"),
        ("economic_mechanism", "mechanism "),
        ("counterfactual_baseline", "   "),
        ("falsification_rule", "\t"),
        ("methodology", " bad tag"),
        ("parameter", " parameter"),
    ),
)
def test_noncanonical_ai_text_is_terminally_audited(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    candidate = _proposal(no_signal_source())
    if field in {"hypothesis", "falsification_rule"}:
        registration = candidate.card.hypothesis.model_copy(update={field: value})
        candidate = replace(
            candidate,
            card=candidate.card.model_copy(update={"hypothesis": registration}),
        )
    elif field in {"economic_mechanism", "counterfactual_baseline"}:
        candidate = replace(candidate, card=candidate.card.model_copy(update={field: value}))
    elif field == "methodology":
        candidate = replace(
            candidate,
            strategy_draft=replace(candidate.strategy_draft, methodology_tags=(value,)),
        )
    else:
        candidate = replace(
            candidate,
            strategy_draft=replace(candidate.strategy_draft, free_parameters=(value,)),
        )

    result = _loop(tmp_path, _SequenceGenerator([candidate])).run(_view())
    reader = ExperimentLedgerStore(tmp_path / "ledger.sqlite3").reader()
    versions = reader.day_hypothesis_versions(market_id=_view().market_id)

    assert result.accepted is False
    assert result.drafts_attempted == 1
    assert result.remaining_budget < _view().search_budget
    assert result.terminal_reason is not None
    if field in {"methodology", "parameter"}:
        assert len(versions) == 1
    else:
        assert versions == ()
    state = reader.day_discovery_cycle_state(result.cycle_id)
    assert state.events[-2].event_kind.value == "branch_finalized"
