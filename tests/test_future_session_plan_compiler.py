from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.test_forward_runtime_readiness_cli import (
    _git,
    _runtime,
    _stores,
)
from tests.test_kis_kr_session_calendar import _receipt
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
    FutureSessionUsRole,
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_plan_json,
)
from trading_agent.future_session_us_payloads import build_us_jobs
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_research_registration import (
    register_kr_theme_research_manifest,
)
from trading_agent.kr_theme_research_rollover import (
    prepare_kr_theme_research_rollover,
)
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketStrategyVersionRegistration,
)
from trading_agent.multi_market_lifecycle_keys import (
    multi_market_lifecycle_event_key,
)
from trading_agent.multi_market_lifecycle_models import (
    MultiMarketStrategyLifecycleEvent,
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


def test_xnys_2026_07_31_post_close_swing_role_timing_contract(tmp_path: Path) -> None:
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
    target = dt.date(2026, 7, 31)
    watch_database = (
        runtime
        / "outputs"
        / "future-sessions"
        / "us"
        / target.isoformat()
        / "watch"
        / "paper_recommendations.sqlite3"
    )
    jobs = build_us_jobs(
        request.model_copy(update={"watch_database": watch_database}),
        target,
        "orb_fixture",
    )

    # Then
    assert tuple(job.role.value for job in jobs if job.role is not None) == (
        "us_orb_watcher",
        "us_hermes_projection",
        "us_day_preflight_observer",
        "us_day_close_finalizer",
        "us_day_arm_observer",
        "us_research_post_close_swing",
    )
    assert tuple(job.run_at.isoformat() for job in jobs) == (
        "2026-07-31T08:00:00-04:00",
        "2026-07-31T08:00:00-04:00",
        "2026-07-31T08:00:00-04:00",
        "2026-07-31T08:00:00-04:00",
        "2026-07-31T09:00:00-04:00",
        "2026-07-31T16:25:00-04:00",
    )
    assert tuple(
        job.expires_at.isoformat()
        for job in jobs
        if job.expires_at is not None
    ) == (
        "2026-07-31T16:20:00-04:00",
        "2026-07-31T16:20:00-04:00",
        "2026-07-31T15:35:00-04:00",
        "2026-07-31T16:20:00-04:00",
        "2026-07-31T15:31:00-04:00",
        "2026-07-31T17:30:00-04:00",
    )
    assert "open=09:30" in jobs[4].purpose
    assert "entry_cutoff=15:30" in jobs[4].purpose
    assert "finalize=16:05" in jobs[3].purpose
    assert "source_deadline=16:15" in jobs[3].purpose
    watcher = jobs[0]
    assert watcher.command == (
        str(request.runtime_interpreter),
        str(runtime / "run_kis_paper_watch.py"),
        "--output-dir",
        str(watch_database.parent),
        "--cycles",
        "390",
        "--interval-seconds",
        "60",
        "--max-wait-minutes",
        "720",
        "--top",
        "10",
        "--max-pages",
        "1",
        "--collect-premarket",
        "--premarket-interval-seconds",
        "300",
        "--wait-until-open",
        "--strategy",
        "orb",
        "--lane-execution-database",
        str(request.execution_database),
        "--lane-registry",
        str(request.lane_registry),
        "--lane-review-ledger",
        str(request.lane_review_ledger),
        "--lane-forward-output-dir",
        str(
            request.artifact_root
            / "us"
            / target.isoformat()
            / "lane-forward"
        ),
        "--experiment-ledger",
        str(request.experiment_ledger),
        "--delivery-database",
        str(request.delivery_database),
    )
    assert jobs[1].model_dump(mode="json")["payload_mode"] == (
        "repeat_through_deadline"
    )
    assert jobs[1].model_dump(mode="json")["poll_until"] == (
        "2026-07-31T16:15:00-04:00"
    )
    assert jobs[2].model_dump(mode="json")["payload_mode"] == (
        "retry_until_success"
    )
    assert jobs[2].model_dump(mode="json")["poll_until"] == (
        "2026-07-31T15:30:00-04:00"
    )
    assert jobs[3].model_dump(mode="json")["not_before"] == (
        "2026-07-31T16:05:00-04:00"
    )
    assert jobs[3].model_dump(mode="json")["poll_until"] == (
        "2026-07-31T16:15:00-04:00"
    )
    assert jobs[3].command[jobs[3].command.index("--repository") + 1] == str(
        runtime
    )
    assert jobs[3].command[
        jobs[3].command.index("--source-artifact") + 1
    ] == str(watch_database.relative_to(runtime))
    assert tuple(job.poll_interval_seconds for job in jobs[1:4]) == (5, 5, 5)
    finalizer_gate = jobs[3].model_dump(mode="json")["finalizer_gate"]
    assert finalizer_gate == {
        "source_path": str(watch_database),
        "stability_seconds": 5,
        "watcher_active_probe": [
            "/bin/launchctl",
            "print",
            f"gui/{os.getuid()}/{watcher.label}",
        ],
        "watcher_label": watcher.label,
    }
    assert jobs[4].command[jobs[4].command.index("--repository") + 1] == str(
        runtime
    )
    assert jobs[4].command[
        jobs[4].command.index("--poll-interval-seconds") + 1
    ] == "5"
    swing = jobs[5]
    swing_root = runtime / "outputs" / "us_swing_shadow"
    report_root = swing_root / "operating" / target.isoformat()
    assert swing.role is FutureSessionUsRole.US_RESEARCH_POST_CLOSE_SWING
    assert swing.dependencies == (FutureSessionUsRole.US_DAY_CLOSE_FINALIZER,)
    assert swing.payload_mode.value == "once"
    assert swing.command == (
        str(request.runtime_interpreter),
        str(runtime / "run_us_swing_operating_session.py"),
        "--session-date",
        target.isoformat(),
        "--auto-universe",
        "--feed",
        "sip",
        "--research-manifest",
        str(runtime / "examples" / "research" / "us-swing-new-high-rvol-v1.json"),
        "--experiment-ledger",
        str(request.experiment_ledger),
        "--shadow-ledger",
        str(swing_root / "swing-shadow.sqlite3"),
        "--delivery-store",
        str(request.delivery_database),
        "--review-ledger",
        str(swing_root / "reviews.sqlite3"),
        "--output-dir",
        str(report_root),
    )
    assert swing.source_paths == (
        runtime / "examples" / "research" / "us-swing-new-high-rvol-v1.json",
        request.experiment_ledger,
        swing_root / "swing-shadow.sqlite3",
        request.delivery_database,
        swing_root / "reviews.sqlite3",
    )
    assert swing.destination_paths == (
        request.experiment_ledger,
        swing_root / "swing-shadow.sqlite3",
        request.delivery_database,
        swing_root / "reviews.sqlite3",
        report_root / "us_swing_operating_session_ko.md",
    )
    command_text = " ".join(swing.command)
    assert "--secret-path" not in command_text
    assert not any(
        forbidden in command_text
        for forbidden in (
            "order",
            "account",
            "balance",
            "position",
            "paper-api.alpaca.markets",
            "api.alpaca.markets",
        )
    )


def test_us_job_builder_rejects_noncanonical_watch_database(
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
    ).model_copy(update={"watch_database": (tmp_path / "watch.sqlite3").absolute()})

    # When / Then
    with pytest.raises(ValueError):
        build_us_jobs(request, dt.date(2026, 7, 27), "orb_fixture")


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


def test_stale_scheduler_sha_waits_without_jobs(tmp_path: Path) -> None:
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
    ).model_copy(update={"scheduler_main_sha": "f" * 40})

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, WaitingSessionAuthority)
    assert decision.jobs == ()
    assert tuple(reason.value for reason in decision.reasons) == (
        "scheduler_authority_invalid",
    )


def test_us_plan_distinguishes_store_schema_from_runtime_environment(
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
    schema_decision = compile_future_session_plan(
        request.model_copy(
            update={
                "execution_database": (
                    tmp_path / "uninitialized-execution.sqlite3"
                ).absolute()
            }
        )
    )
    environment_decision = compile_future_session_plan(
        request.model_copy(update={"runtime_interpreter": Path("/usr/bin/false")})
    )

    # Then
    assert isinstance(schema_decision, WaitingSessionAuthority)
    assert tuple(reason.value for reason in schema_decision.reasons) == (
        "frozen_runtime_store_schema_incompatible",
    )
    assert isinstance(environment_decision, WaitingSessionAuthority)
    assert tuple(reason.value for reason in environment_decision.reasons) == (
        "runtime_environment_invalid",
    )


def test_equal_scheduler_and_runtime_sha_is_valid_authority(
    tmp_path: Path,
) -> None:
    # Given
    runtime, required, head = _runtime(tmp_path)
    _git(runtime, "branch", "-M", "main")
    _git(runtime, "update-ref", "refs/remotes/origin/main", head)
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    request = _us_request(
        tmp_path,
        runtime=runtime,
        head=head,
        required=required,
        lane=lane,
        experiment=experiment,
        execution=execution,
    ).model_copy(
        update={
            "authority_repository": runtime,
            "scheduler_main_sha": head,
        }
    )

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, ReadyToPrepareSessionPlan)
    assert decision.scheduler_main_sha == decision.frozen_runtime.commit_sha


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


def test_kr_configured_non_running_interpreter_waits_for_runtime_environment(
    tmp_path: Path,
) -> None:
    # Given
    request, _, _ = _kr_request(tmp_path)
    configured = request.model_copy(
        update={
            "runtime_interpreter": Path("/usr/bin/false"),
            "delivery_database": (tmp_path / "delivery.sqlite3").absolute(),
        }
    )

    # When
    decision = compile_future_session_plan(configured)

    # Then
    assert isinstance(decision, WaitingSessionAuthority)
    assert tuple(reason.value for reason in decision.reasons) == (
        "runtime_environment_invalid",
    )


def test_kr_configured_current_interpreter_keeps_v7_plan_ready(
    tmp_path: Path,
) -> None:
    # Given
    request, _, _ = _kr_request(tmp_path)
    configured = request.model_copy(
        update={
            "runtime_interpreter": Path(sys.executable).absolute(),
            "delivery_database": (tmp_path / "delivery.sqlite3").absolute(),
        }
    )

    # When
    decision = compile_future_session_plan(configured)

    # Then
    assert isinstance(decision, ReadyToPrepareSessionPlan)
    assert decision.target_session == dt.date(2026, 7, 22)


def test_kr_plan_waits_when_frozen_runtime_cannot_read_experiment_ledger(
    tmp_path: Path,
) -> None:
    # Given
    runtime, _, _ = _runtime(tmp_path)
    shutil.copytree(
        Path(__file__).parents[1] / "trading_agent",
        runtime / "trading_agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    schema = runtime / "trading_agent" / "experiment_ledger_schema.py"
    schema.write_text(
        schema.read_text(encoding="utf-8").replace(
            "EXPERIMENT_LEDGER_SCHEMA_VERSION: Final = 7",
            "EXPERIMENT_LEDGER_SCHEMA_VERSION: Final = 6",
        ),
        encoding="utf-8",
    )
    _git(runtime, "add", "trading_agent")
    _git(runtime, "commit", "--quiet", "-m", "older ledger reader")
    request, _, _ = _kr_request(tmp_path, runtime=runtime)

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, WaitingSessionAuthority)
    assert tuple(reason.value for reason in decision.reasons) == (
        "frozen_runtime_store_schema_incompatible",
    )


@pytest.mark.parametrize(
    "lifecycle",
    ("absent", "rejected"),
)
def test_kr_non_shadow_target_lifecycle_waits_without_jobs(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    # Given
    request, _, _ = _kr_request(tmp_path, lifecycle=lifecycle)

    # When
    decision = compile_future_session_plan(request)

    # Then
    assert isinstance(decision, WaitingSessionAuthority)
    assert decision.jobs == ()
    assert tuple(reason.value for reason in decision.reasons) == (
        "runtime_authority_missing",
    )


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
    authority, scheduler_sha = _authority_repository(tmp_path)
    return FutureSessionPlanRequest(
        market=FutureSessionMarket.US,
        after_date=dt.date(2026, 7, 24),
        compiled_at=dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
        scheduler_main_sha=scheduler_sha,
        authority_repository=authority,
        frozen_runtime=FrozenRuntimeAuthority(
            directory=runtime,
            commit_sha=head,
        ),
        artifact_root=(tmp_path / "artifacts").absolute(),
        experiment_ledger=experiment.absolute(),
        lane_registry=lane.absolute(),
        execution_database=execution.absolute(),
        required_runtime_commits=(required,),
        runtime_interpreter=Path(sys.executable).absolute(),
        watch_database=(
            runtime
            / "outputs"
            / "future-sessions"
            / "us"
            / "2026-07-27"
            / "watch"
            / "paper_recommendations.sqlite3"
        ).absolute(),
        delivery_database=(tmp_path / "delivery.sqlite3").absolute(),
        arm_database=(tmp_path / "arm.sqlite3").absolute(),
        signing_key=(tmp_path / "signing.env").absolute(),
        opportunity_outbox=(tmp_path / "opportunities.sqlite3").absolute(),
        signal_outbox=(tmp_path / "signals.sqlite3").absolute(),
        lane_review_ledger=(tmp_path / "lane-review.sqlite3").absolute(),
    )


def _kr_request(
    tmp_path: Path,
    *,
    lifecycle: str = "shadow",
    runtime: Path | None = None,
) -> tuple[
    FutureSessionPlanRequest,
    ExperimentLedgerStore,
    MultiMarketStrategyVersionRegistration,
]:
    if runtime is None:
        runtime, _, head = _runtime(tmp_path)
    else:
        head = _git(runtime, "rev-parse", "HEAD")
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
    if lifecycle != "absent":
        _seed_kr_lifecycle(
            ledger,
            day_version,
            rejected=lifecycle == "rejected",
        )
    authority, scheduler_sha = _authority_repository(tmp_path)
    request = FutureSessionPlanRequest(
        market=FutureSessionMarket.KR,
        after_date=dt.date(2026, 7, 20),
        compiled_at=dt.datetime(2026, 7, 20, 9, tzinfo=dt.UTC),
        scheduler_main_sha=scheduler_sha,
        authority_repository=authority,
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


def _authority_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "authority"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.email", "authority@example.invalid")
    _git(repository, "config", "user.name", "Authority Test")
    (repository / "authority.txt").write_text("main\n", encoding="utf-8")
    _git(repository, "add", "authority.txt")
    _git(repository, "commit", "--quiet", "-m", "authority")
    head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/remotes/origin/main", head)
    return repository, head


def _seed_kr_lifecycle(
    ledger: ExperimentLedgerStore,
    version: MultiMarketStrategyVersionRegistration,
    *,
    rejected: bool,
) -> None:
    hypothesis = next(
        item.registration
        for item in ledger.multi_market_hypotheses()
        if item.registration.hypothesis_id == version.hypothesis_id
    )
    calendar_id = "a" * 64
    registration = MultiMarketStrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        strategy_lane=version.strategy_lane,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="future_session_fixture_v1",
        decision_session_date=dt.date(2026, 7, 20),
        effective_session_date=dt.date(
            2026,
            7,
            21 if rejected else 22,
        ),
        decided_at=dt.datetime(
            2026,
            7,
            20,
            18,
            tzinfo=dt.timezone(dt.timedelta(hours=9)),
        ),
        session_calendar_snapshot_id=calendar_id,
        evidence_keys=tuple(
            sorted(
                (
                    calendar_id,
                    version.experiment_scope_key,
                    str(multi_market_hypothesis_registration_key(hypothesis)),
                    str(multi_market_strategy_version_registration_key(version)),
                )
            )
        ),
        reason_codes=("multi_market_strategy_registered",),
        previous_event_key=None,
    )
    with ledger.writer() as writer:
        assert writer.append_multi_market_lifecycle_event(registration) is True
        if rejected:
            previous = str(multi_market_lifecycle_event_key(registration))
            transition = MultiMarketStrategyLifecycleEvent(
                strategy_version=version.strategy_version,
                strategy_lane=version.strategy_lane,
                sequence=2,
                event_kind=StrategyLifecycleEventKind.TRANSITION,
                from_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
                to_state=StrategyLifecycleState.REJECTED,
                policy_version="future_session_fixture_v1",
                decision_session_date=dt.date(2026, 7, 21),
                effective_session_date=dt.date(2026, 7, 22),
                decided_at=dt.datetime(2026, 7, 21, 15, 40, tzinfo=dt.timezone(dt.timedelta(hours=9))),
                session_calendar_snapshot_id="b" * 64,
                evidence_keys=tuple(sorted((previous, "b" * 64))),
                reason_codes=("fixture_rejected",),
                previous_event_key=previous,
            )
            assert writer.append_multi_market_lifecycle_event(transition) is True
