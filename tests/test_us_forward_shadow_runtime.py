from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.us_forward_shadow_support import (
    failing_source,
    no_signal_source,
    prepared_runtime,
    shadow_tick,
    signal_source,
)
from trading_agent.day_forward_trial_identity import DayForwardTrialEventKind
from trading_agent.us_forward_shadow_models import UsForwardShadowStatus
from trading_agent.us_forward_shadow_runtime import (
    InvalidUsForwardShadowRuntimeError,
)
from trading_agent.us_forward_shadow_runtime import (
    run_us_forward_shadow_tick as _run_us_forward_shadow_tick,
)
from trading_agent.us_forward_shadow_trial import completed_bar_at


def run_us_forward_shadow_tick(tick, services):
    return _run_us_forward_shadow_tick(tick, services, evaluation_at=tick.observed_at)


def test_no_signal_trial_is_future_only_and_tick_replay_is_idempotent(tmp_path: Path) -> None:
    # Given a real no-signal generated capsule selected by the stored US policy.
    services, _ = prepared_runtime(tmp_path, source=no_signal_source())
    registration_tick = shadow_tick(services, 1, 1)
    eligible_tick = shadow_tick(services, 2, 2)

    # When registration, first eligible observation, and exact replay run.
    registered = run_us_forward_shadow_tick(registration_tick, services)
    observed = run_us_forward_shadow_tick(eligible_tick, services)
    replayed = run_us_forward_shadow_tick(eligible_tick, services)

    # Then no backdated event exists and replay adds no duplicate.
    state = services.ledger.reader().day_forward_trials()[0]
    assert registered.results[0].status is UsForwardShadowStatus.REGISTERED
    assert observed.results[0].status is UsForwardShadowStatus.NO_SIGNAL
    assert replayed.results[0].status is UsForwardShadowStatus.REPLAYED
    assert tuple(event.event_kind for event in state.events) == (DayForwardTrialEventKind.NO_SIGNAL,)


def test_r1_observation_keeps_trial_open_until_r2_then_records_equal_weight_legs(tmp_path: Path) -> None:
    # Given one sandboxed strategy that emits a long signal only for AAPL.
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)

    # When future bars enter, reach 1R only, then reach the final 2R target.
    entered = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)
    r1_observed = run_us_forward_shadow_tick(shadow_tick(services, 3, 3, high=102.2), services)
    exited = run_us_forward_shadow_tick(shadow_tick(services, 4, 4, high=103.2), services)

    # Then append-only state and immutable modeled outcome survive fresh readers.
    state = services.ledger.reader().day_forward_trials()[0]
    assert [entered.results[0].status, r1_observed.results[0].status, exited.results[0].status] == [
        UsForwardShadowStatus.ENTERED,
        UsForwardShadowStatus.OBSERVED,
        UsForwardShadowStatus.EXITED,
    ]
    assert tuple(event.event_kind for event in state.events) == (
        DayForwardTrialEventKind.SIGNAL,
        DayForwardTrialEventKind.ENTRY,
        DayForwardTrialEventKind.OBSERVED,
        DayForwardTrialEventKind.EXIT,
    )
    outcome_id = exited.results[0].outcome_id
    assert outcome_id is not None
    outcome = services.shadow_artifacts.outcome(outcome_id)
    assert outcome.modeled is True and outcome.profitability_claim is False
    assert [(leg.target_label, leg.exit_reason.value, leg.weight) for leg in outcome.legs] == [
        ("r1", "target", Decimal("0.5")),
        ("r2", "target", Decimal("0.5")),
    ]


def test_same_bar_stop_target_collision_resolves_to_stop(tmp_path: Path) -> None:
    # Given an entered signal whose next bar crosses both stop and first target.
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)
    _ = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)

    # When the collision bar is evaluated.
    result = run_us_forward_shadow_tick(shadow_tick(services, 3, 3, low=99.9, high=103.2), services)

    # Then the conservative stop outcome is immutable.
    outcome_id = result.results[0].outcome_id
    assert outcome_id is not None
    outcome = services.shadow_artifacts.outcome(outcome_id)
    assert outcome.exit_reason.value == "stop"
    assert [(leg.target_label, leg.exit_reason.value) for leg in outcome.legs] == [
        ("r1", "stop"),
        ("r2", "stop"),
    ]


def test_bar_gap_censors_before_outcome_and_wrong_policy_mutates_nothing(tmp_path: Path) -> None:
    # Given one registered trial and an unrelated policy identity.
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)
    before = services.ledger.reader().day_forward_trials()[0]

    # When the policy is wrong it blocks, and a later sequence gap is censored.
    with pytest.raises(InvalidUsForwardShadowRuntimeError, match="policy_missing"):
        run_us_forward_shadow_tick(shadow_tick(services, 2, 2, policy_id="e" * 64), services)
    after_block = services.ledger.reader().day_forward_trials()[0]
    censored = run_us_forward_shadow_tick(shadow_tick(services, 4, 4), services)

    # Then the blocked call is mutation-free and the gap becomes terminal.
    assert after_block == before
    assert censored.results[0].status is UsForwardShadowStatus.CENSORED
    assert services.ledger.reader().day_forward_trials()[0].terminal is True


def test_generated_failure_is_terminal_and_redacted(tmp_path: Path) -> None:
    # Given a capsule that passes preflight but raises only on the live-symbol research bar.
    services, _ = prepared_runtime(tmp_path, source=failing_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)

    # When the first future-only eligible bar reaches the sandbox.
    failed = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)

    # Then a stable redacted failure is terminal in the audit ledger.
    assert failed.results[0].status is UsForwardShadowStatus.FAILED
    assert failed.results[0].reason_codes == ("generated_strategy_failed",)
    state = services.ledger.reader().day_forward_trials()[0]
    assert state.terminal is True
    assert state.events[-1].event_kind is DayForwardTrialEventKind.FAILED


def test_host_admission_uses_deterministic_completed_bar_times_for_artifact_identities(tmp_path: Path) -> None:
    baseline = _host_timed_signal_run(tmp_path / "baseline", shift_untrusted_observed_at=False)
    shifted = _host_timed_signal_run(tmp_path / "shifted", shift_untrusted_observed_at=True)

    baseline_state, baseline_signal, baseline_outcome, host_times = baseline
    shifted_state, shifted_signal, shifted_outcome, _ = shifted

    assert baseline_state.trial.trial_id == shifted_state.trial.trial_id
    assert baseline_state.trial.preregistered_at == host_times[0]
    assert tuple(event.event_id for event in baseline_state.events) == tuple(
        event.event_id for event in shifted_state.events
    )
    assert tuple(event.event_at for event in baseline_state.events) == (
        host_times[1] - dt.timedelta(seconds=5),
        host_times[1] - dt.timedelta(seconds=5),
        host_times[2] - dt.timedelta(seconds=5),
        host_times[3] - dt.timedelta(seconds=5),
    )
    assert baseline_signal.artifact_id == shifted_signal.artifact_id
    assert baseline_signal.signal.observed_at == host_times[1] - dt.timedelta(seconds=5)
    assert baseline_outcome.outcome_id == shifted_outcome.outcome_id
    assert baseline_outcome.recorded_at == host_times[3] - dt.timedelta(seconds=5)


def test_completed_bar_time_uses_one_minute_boundary_despite_warmup_spacing(
    tmp_path: Path,
) -> None:
    # Given a valid current bar preceded by two-minute warmup bars.
    services, _ = prepared_runtime(tmp_path, source=no_signal_source())
    tick = shadow_tick(services, 3, 3)
    warmup_bars = (
        tick.bars[0].model_copy(update={"timestamp": tick.bars[0].timestamp - dt.timedelta(minutes=2)}),
        tick.bars[1].model_copy(update={"timestamp": tick.bars[1].timestamp - dt.timedelta(minutes=1)}),
        tick.bars[2],
    )
    spaced_tick = tick.model_validate(tick.model_dump(mode="python") | {"bars": warmup_bars})

    # When the completed-bar boundary is derived for artifact identities.
    completed_at = completed_bar_at(spaced_tick)

    # Then it is exactly one minute after the validated latest bar.
    assert completed_at == tick.bars[-1].timestamp + dt.timedelta(minutes=1)


def test_restart_recovers_signal_artifact_before_ledger_and_preserves_r1_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)
    publish_signal = services.shadow_artifacts.publish_signal

    def publish_then_crash(artifact):
        _ = publish_signal(artifact)
        raise RuntimeError("injected_signal_publication_crash")

    monkeypatch.setattr(services.shadow_artifacts, "publish_signal", publish_then_crash)
    with pytest.raises(RuntimeError, match="injected_signal_publication_crash"):
        _ = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)
    monkeypatch.setattr(services.shadow_artifacts, "publish_signal", publish_signal)
    published_signal = services.shadow_artifacts.signal(services.ledger.reader().day_forward_trials()[0].trial.trial_id)

    recovered = run_us_forward_shadow_tick(shadow_tick(services, 3, 3, high=102.2), services)
    state = services.ledger.reader().day_forward_trials()[0]

    assert recovered.results[0].status is UsForwardShadowStatus.OBSERVED
    assert tuple(event.event_kind for event in state.events) == (
        DayForwardTrialEventKind.SIGNAL,
        DayForwardTrialEventKind.ENTRY,
        DayForwardTrialEventKind.OBSERVED,
    )
    assert state.events[-1].reason_codes == ("target_r1_reached",)
    assert services.shadow_artifacts.signal(state.trial.trial_id).artifact_id == published_signal.artifact_id
    assert len(tuple((tmp_path / "shadow" / "signals").glob("*.json"))) == 1


def test_restart_recovers_outcome_artifact_before_exit_ledger_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)
    _ = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)
    _ = run_us_forward_shadow_tick(shadow_tick(services, 3, 3, high=102.2), services)
    publish_outcome = services.shadow_artifacts.publish_outcome

    def publish_then_crash(artifact):
        _ = publish_outcome(artifact)
        raise RuntimeError("injected_outcome_publication_crash")

    monkeypatch.setattr(services.shadow_artifacts, "publish_outcome", publish_then_crash)
    with pytest.raises(RuntimeError, match="injected_outcome_publication_crash"):
        _ = run_us_forward_shadow_tick(shadow_tick(services, 4, 4, high=103.2), services)
    monkeypatch.setattr(services.shadow_artifacts, "publish_outcome", publish_outcome)
    published_outcome = services.shadow_artifacts.outcome_for_trial(
        services.ledger.reader().day_forward_trials()[0].trial.trial_id
    )

    recovery_tick = shadow_tick(services, 5, 5)
    recovered_exit_bar = recovery_tick.bars[-2].model_copy(update={"high": 103.2})
    recovery_bars = (*recovery_tick.bars[:-2], recovered_exit_bar, recovery_tick.bars[-1])
    recovered = run_us_forward_shadow_tick(
        recovery_tick.model_copy(update={"bars": recovery_bars}),
        services,
    )
    state = services.ledger.reader().day_forward_trials()[0]
    outcome = services.shadow_artifacts.outcome_for_trial(state.trial.trial_id)

    assert recovered.results[0].status is UsForwardShadowStatus.EXITED
    assert tuple(event.event_kind for event in state.events) == (
        DayForwardTrialEventKind.SIGNAL,
        DayForwardTrialEventKind.ENTRY,
        DayForwardTrialEventKind.OBSERVED,
        DayForwardTrialEventKind.EXIT,
    )
    assert outcome is not None
    assert published_outcome is not None
    assert recovered.results[0].outcome_id == published_outcome.outcome_id == outcome.outcome_id
    assert len(tuple((tmp_path / "shadow" / "outcomes").glob("*.json"))) == 1


@pytest.mark.parametrize(
    "evaluation_at",
    (
        dt.datetime(2026, 8, 21, 14, 1, 30, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 20, 21, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 20, 14, 3, 30, tzinfo=dt.UTC),
    ),
)
def test_runtime_rejects_noncurrent_or_stale_host_tick_before_ledger_read(
    tmp_path: Path,
    evaluation_at: dt.datetime,
) -> None:
    services, _ = prepared_runtime(tmp_path, source=no_signal_source())
    tick = shadow_tick(services, 1, 1)

    with pytest.raises(InvalidUsForwardShadowRuntimeError, match="tick_not_current"):
        _run_us_forward_shadow_tick(tick, services, evaluation_at=evaluation_at)

    assert services.ledger.reader().day_forward_trials() == ()


def _host_timed_signal_run(
    root: Path,
    *,
    shift_untrusted_observed_at: bool,
):
    services, _ = prepared_runtime(root, source=signal_source())
    ticks = (
        shadow_tick(services, 1, 1),
        shadow_tick(services, 2, 2),
        shadow_tick(services, 3, 3, high=102.2),
        shadow_tick(services, 4, 4, high=103.2),
    )
    host_times = tuple(tick.observed_at for tick in ticks)
    outcome_id: str | None = None
    for tick, evaluation_at in zip(ticks, host_times, strict=True):
        checked = (
            tick.model_copy(update={"observed_at": tick.observed_at - dt.timedelta(seconds=1)})
            if shift_untrusted_observed_at
            else tick
        )
        result = _run_us_forward_shadow_tick(checked, services, evaluation_at=evaluation_at)
        outcome_id = result.results[0].outcome_id or outcome_id
    state = services.ledger.reader().day_forward_trials()[0]
    signal = services.shadow_artifacts.signal(state.trial.trial_id)
    assert outcome_id is not None
    return state, signal, services.shadow_artifacts.outcome(outcome_id), host_times
