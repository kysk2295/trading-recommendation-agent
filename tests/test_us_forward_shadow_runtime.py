from __future__ import annotations

import datetime as dt
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


def test_signal_entry_observed_exit_survives_restart(tmp_path: Path) -> None:
    # Given one sandboxed strategy that emits a long signal only for AAPL.
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)

    # When future bars enter, remain between stop and target, then hit target.
    entered = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)
    observed = run_us_forward_shadow_tick(shadow_tick(services, 3, 3), services)
    exited = run_us_forward_shadow_tick(shadow_tick(services, 4, 4, high=102.2), services)

    # Then append-only state and immutable modeled outcome survive fresh readers.
    state = services.ledger.reader().day_forward_trials()[0]
    assert [entered.results[0].status, observed.results[0].status, exited.results[0].status] == [
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


def test_same_bar_stop_target_collision_resolves_to_stop(tmp_path: Path) -> None:
    # Given an entered signal whose next bar crosses both stop and first target.
    services, _ = prepared_runtime(tmp_path, source=signal_source())
    _ = run_us_forward_shadow_tick(shadow_tick(services, 1, 1), services)
    _ = run_us_forward_shadow_tick(shadow_tick(services, 2, 2), services)

    # When the collision bar is evaluated.
    result = run_us_forward_shadow_tick(shadow_tick(services, 3, 3, low=99.9, high=102.2), services)

    # Then the conservative stop outcome is immutable.
    outcome_id = result.results[0].outcome_id
    assert outcome_id is not None
    assert services.shadow_artifacts.outcome(outcome_id).exit_reason.value == "stop"


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


@pytest.mark.parametrize(
    "evaluation_at",
    (
        dt.datetime(2026, 8, 21, 14, 1, 30, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 20, 21, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 20, 14, 2, 30, tzinfo=dt.UTC),
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
