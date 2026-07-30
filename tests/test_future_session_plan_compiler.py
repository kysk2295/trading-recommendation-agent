from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_forward_runtime_readiness_cli import (
    _runtime,
    _stores,
)
from tests.test_kis_kr_session_calendar import _receipt
from trading_agent.experiment_ledger_models import TrialKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_plan_json,
)
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_research_registration import (
    register_kr_theme_research_manifest,
)
from trading_agent.kr_theme_research_rollover import (
    prepare_kr_theme_research_rollover,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketStrategyVersionRegistration,
)
from trading_agent.multi_market_trial_models import (
    MultiMarketExperimentTrialRegistration,
)
from trading_agent.us_equity_calendar import (
    UnsupportedUsEquityCalendarDateError,
    next_regular_session,
    regular_session_bounds,
)


@pytest.mark.parametrize(
    ("after", "expected"),
    (
        (dt.date(2026, 7, 2), dt.date(2026, 7, 6)),
        (dt.date(2026, 11, 25), dt.date(2026, 11, 27)),
        (dt.date(2026, 7, 27), dt.date(2026, 7, 28)),
    ),
)
def test_next_regular_session_uses_tracked_xnys_calendar(
    after: dt.date,
    expected: dt.date,
) -> None:
    # Given / When
    actual = next_regular_session(after)

    # Then
    assert actual == expected
    assert regular_session_bounds(actual) is not None


def test_next_regular_session_rejects_untracked_boundary() -> None:
    # Given / When / Then
    with pytest.raises(UnsupportedUsEquityCalendarDateError):
        next_regular_session(dt.date(2028, 12, 31))


def test_us_plan_is_stable_and_binds_exact_runtime_authority(
    tmp_path: Path,
) -> None:
    # Given
    runtime, required, head = _runtime(tmp_path)
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    request = _us_request(
        tmp_path,
        runtime=runtime,
        head=head,
        required=required,
        lane=lane,
        experiment=experiment,
        execution=execution,
    )

    # When
    first = compile_future_session_plan(request)
    replay = compile_future_session_plan(request)

    # Then
    assert isinstance(first, ReadyToPrepareSessionPlan)
    assert first == replay
    assert first.target_session == dt.date(2026, 7, 27)
    assert len(first.strategy_registrations) == 4
    assert first.frozen_runtime.commit_sha == head
    assert first.scheduler_main_sha != head
    assert canonical_plan_json(first) == canonical_plan_json(replay)
    assert not request.artifact_root.exists()
    tampered = first.model_dump(mode="python")
    tampered["scheduler_main_sha"] = "c" * 40
    with pytest.raises(ValueError):
        ReadyToPrepareSessionPlan.model_validate(tampered)


def test_us_plan_waits_for_wrong_explicit_runtime_authority(
    tmp_path: Path,
) -> None:
    # Given
    runtime, required, head = _runtime(tmp_path)
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    request = _us_request(
        tmp_path,
        runtime=runtime,
        head="f" * 40,
        required=required,
        lane=lane,
        experiment=experiment,
        execution=execution,
    )

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, WaitingSessionAuthority)
    assert decision.jobs == ()
    assert tuple(reason.value for reason in decision.reasons) == (
        "frozen_runtime_invalid",
    )


def test_kr_old_snapshot_derives_schedule_but_trial_stays_deferred(
    tmp_path: Path,
) -> None:
    # Given
    request, _, _ = _kr_request(tmp_path)

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, ReadyToPrepareSessionPlan)
    assert decision.target_session == dt.date(2026, 7, 22)
    assert (
        decision.trial_registration_state.value
        == "deferred_until_preopen"
    )
    assert decision.calendar_provenance.observed_at is not None
    assert decision.calendar_provenance.observed_at.date() == dt.date(2026, 7, 20)
    assert len(decision.strategy_registrations) == 2


def test_kr_conflicting_target_trial_blocks_materializable_jobs(
    tmp_path: Path,
) -> None:
    # Given
    request, ledger, day_version = _kr_request(tmp_path)
    trial = MultiMarketExperimentTrialRegistration(
        trial_id="conflicting-kr-trial",
        strategy_version=day_version.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=next(
            item.registration.experiment_scope
            for item in ledger.multi_market_hypotheses()
            if item.registration.hypothesis_id == day_version.hypothesis_id
        ),
        experiment_scope_key=day_version.experiment_scope_key,
        strategy_lane=day_version.strategy_lane,
        evaluator_version="fixture-v1",
        data_version="d" * 64,
        feed_entitlement="read-only fixture",
        planned_start=dt.date(2026, 7, 22),
        planned_end=dt.date(2026, 7, 22),
        registered_at=dt.datetime(2026, 7, 21, 7, tzinfo=dt.UTC),
        evidence_budget=("calendar_snapshot=fixture",),
    )
    with ledger.writer() as writer:
        assert writer.register_multi_market_trial(trial) is True

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, WaitingSessionAuthority)
    assert decision.jobs == ()
    assert tuple(reason.value for reason in decision.reasons) == (
        "trial_authority_conflict",
    )


def _us_request(
    tmp_path: Path,
    *,
    runtime: Path,
    head: str,
    required: str,
    lane: Path,
    experiment: Path,
    execution: Path,
) -> FutureSessionPlanRequest:
    return FutureSessionPlanRequest(
        market=FutureSessionMarket.US,
        after_date=dt.date(2026, 7, 24),
        compiled_at=dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
        scheduler_main_sha="e" * 40,
        frozen_runtime=FrozenRuntimeAuthority(
            directory=runtime,
            commit_sha=head,
        ),
        artifact_root=(tmp_path / "artifacts").absolute(),
        experiment_ledger=experiment.absolute(),
        lane_registry=lane.absolute(),
        execution_database=execution.absolute(),
        required_runtime_commits=(required,),
    )


def _kr_request(
    tmp_path: Path,
) -> tuple[
    FutureSessionPlanRequest,
    ExperimentLedgerStore,
    MultiMarketStrategyVersionRegistration,
]:
    runtime, _, head = _runtime(tmp_path)
    ledger = ExperimentLedgerStore(tmp_path / "kr-experiment.sqlite3")
    examples = Path(__file__).parents[1] / "examples" / "kr_theme_projection"
    opportunity = examples / "research-registration.json"
    day = examples / "day-research-registration.json"
    _ = register_kr_theme_research_manifest(opportunity, ledger)
    _ = register_kr_theme_research_manifest(day, ledger)
    rollover = prepare_kr_theme_research_rollover(
        experiment_ledger=ledger,
        opportunity_manifest_path=opportunity,
        day_manifest_path=day,
        policy_path=examples / "same-cycle-opportunity-policy.json",
        output_dir=tmp_path / "rollover",
        code_version=head,
        recorded_at=dt.datetime(2026, 7, 20, 8, tzinfo=dt.UTC),
    )
    receipt = _receipt()
    calendar = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    assert calendar.append(receipt, project_kis_kr_session_calendar(receipt)) is True
    versions = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.code_version == head
    )
    day_version = next(
        item for item in versions if "leader-vwap-reclaim" in item.strategy_version
    )
    request = FutureSessionPlanRequest(
        market=FutureSessionMarket.KR,
        after_date=dt.date(2026, 7, 20),
        compiled_at=dt.datetime(2026, 7, 20, 9, tzinfo=dt.UTC),
        scheduler_main_sha="e" * 40,
        frozen_runtime=FrozenRuntimeAuthority(
            directory=runtime,
            commit_sha=head,
        ),
        artifact_root=(tmp_path / "artifacts").absolute(),
        experiment_ledger=ledger.path.absolute(),
        kr_calendar_store=calendar.path.absolute(),
        kr_rollover_bundle=rollover.bundle_path.absolute(),
    )
    return request, ledger, day_version
