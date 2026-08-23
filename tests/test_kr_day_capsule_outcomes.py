from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.kr_day_capsule_outcomes import (
    InvalidKrDayCapsuleOutcomeError,
    KrDayCapsuleOutcomeAttempt,
    KrDayCapsuleTerminalKind,
    project_kr_day_capsule_outcome,
)
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)

KST = dt.timezone(dt.timedelta(hours=9))
SESSION = dt.date(2026, 8, 21)
CURSOR = dt.datetime(2026, 8, 21, 10, 1, tzinfo=KST)


def test_projected_stop_is_terminal_and_uses_fixed_exit_cost() -> None:
    # Given: a contiguous entry-to-stop capsule attempt.
    active = _event(KrDayCapsuleShadowStatus.ACTIVE, KrDayCapsuleShadowReason.ENTRY)
    stopped = _event(
        KrDayCapsuleShadowStatus.STOPPED,
        KrDayCapsuleShadowReason.STOP_FIRST,
        cursor=CURSOR + dt.timedelta(minutes=1),
        previous_event_id=active.event_id,
    )

    # When: the terminal outcome is projected.
    outcome = project_kr_day_capsule_outcome(_attempt((active, stopped)))

    # Then: stop-first remains unfavorable after the fixed exit slippage.
    assert outcome.kind is KrDayCapsuleTerminalKind.EXIT
    assert outcome.reason == "stopped"
    assert outcome.net_return == Decimal("-0.0217623762376237623762376238")


@pytest.mark.parametrize(
    ("status", "reason", "kind"),
    (
        (KrDayCapsuleShadowStatus.REGISTERED, KrDayCapsuleShadowReason.NO_SIGNAL, KrDayCapsuleTerminalKind.NO_SIGNAL),
        (KrDayCapsuleShadowStatus.BLOCKED, KrDayCapsuleShadowReason.SIGNAL_BLOCKED, KrDayCapsuleTerminalKind.BLOCKED),
        (KrDayCapsuleShadowStatus.FAILED, KrDayCapsuleShadowReason.INVALID_EVALUATION, KrDayCapsuleTerminalKind.FAILED),
        (KrDayCapsuleShadowStatus.CENSORED, KrDayCapsuleShadowReason.BAR_GAP, KrDayCapsuleTerminalKind.CENSORED),
    ),
)
def test_non_exit_attempts_are_preserved(
    status: KrDayCapsuleShadowStatus,
    reason: KrDayCapsuleShadowReason,
    kind: KrDayCapsuleTerminalKind,
) -> None:
    # Given: a terminal non-exit Shadow attempt.
    event = _event(status, reason, positioned=status is KrDayCapsuleShadowStatus.ACTIVE)

    # When: its outcome is projected.
    outcome = project_kr_day_capsule_outcome(_attempt((event,)))

    # Then: the attempt remains visible to review.
    assert outcome.kind is kind
    assert outcome.net_return is None


def test_missing_terminal_or_gapped_event_chain_is_rejected() -> None:
    # Given: an unresolved active attempt and a non-contiguous terminal event.
    active = _event(KrDayCapsuleShadowStatus.ACTIVE, KrDayCapsuleShadowReason.ENTRY)
    gapped = _event(
        KrDayCapsuleShadowStatus.TARGETED,
        KrDayCapsuleShadowReason.TARGET,
        cursor=CURSOR + dt.timedelta(minutes=2),
        previous_event_id=active.event_id,
    )

    # When / Then: neither can become a favorable inferred outcome.
    with pytest.raises(InvalidKrDayCapsuleOutcomeError):
        _ = project_kr_day_capsule_outcome(_attempt((active,)))
    with pytest.raises(InvalidKrDayCapsuleOutcomeError):
        _ = project_kr_day_capsule_outcome(_attempt((active, gapped)))


def test_gap_censor_is_countable_but_cannot_advance_the_accepted_cursor() -> None:
    # Given: G003's legitimate active-to-gap-censor event sequence.
    active = _event(KrDayCapsuleShadowStatus.ACTIVE, KrDayCapsuleShadowReason.ENTRY)
    censored = _event(
        KrDayCapsuleShadowStatus.CENSORED,
        KrDayCapsuleShadowReason.BAR_GAP,
        cursor=CURSOR + dt.timedelta(minutes=2),
        previous_event_id=active.event_id,
    )

    # When: the terminal attempt is projected.
    outcome = project_kr_day_capsule_outcome(_attempt((active, censored)))

    # Then: censorship is retained rather than discarded as a missing outcome.
    assert outcome.kind is KrDayCapsuleTerminalKind.CENSORED
    assert outcome.reason == KrDayCapsuleShadowReason.BAR_GAP.value


def test_completed_session_active_position_uses_declared_time_exit_price() -> None:
    # Given: an active position on the completed 15:30 XKRX bar.
    close_cursor = dt.datetime(2026, 8, 21, 15, 30, tzinfo=KST)
    active = _event(
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowReason.ACTIVE,
        cursor=close_cursor,
    )
    attempt = _attempt((active,)).model_copy(update={"session_close_price": Decimal("100")})

    # When: the terminal outcome is projected.
    outcome = project_kr_day_capsule_outcome(attempt)

    # Then: it is a cost-adjusted time exit, not a favorable inferred target.
    assert outcome.kind is KrDayCapsuleTerminalKind.EXIT
    assert outcome.reason == "time_exit"
    assert outcome.net_return == Decimal("-0.0118811881188118811881188119")


def test_exact_replay_has_the_same_immutable_outcome_id() -> None:
    # Given: one blocked terminal attempt.
    attempt = _attempt((_event(KrDayCapsuleShadowStatus.BLOCKED, KrDayCapsuleShadowReason.SIGNAL_BLOCKED),))

    # When: it is projected twice.
    first = project_kr_day_capsule_outcome(attempt)
    second = project_kr_day_capsule_outcome(attempt)

    # Then: the complete payload has one stable identity.
    assert first == second
    assert first.outcome_id == second.outcome_id


def _attempt(events: tuple[KrDayCapsuleShadowEvent, ...]) -> KrDayCapsuleOutcomeAttempt:
    return KrDayCapsuleOutcomeAttempt(
        attempt_id="attempt-1",
        capsule_id="a" * 64,
        hypothesis_version_id="b" * 64,
        trial_id="trial-1",
        session_date=SESSION,
        events=events,
    )


def _event(
    status: KrDayCapsuleShadowStatus,
    reason: KrDayCapsuleShadowReason,
    *,
    cursor: dt.datetime = CURSOR,
    previous_event_id: str | None = None,
    positioned: bool = True,
) -> KrDayCapsuleShadowEvent:
    has_position = positioned and status not in {
        KrDayCapsuleShadowStatus.REGISTERED,
        KrDayCapsuleShadowStatus.BLOCKED,
        KrDayCapsuleShadowStatus.FAILED,
        KrDayCapsuleShadowStatus.CENSORED,
    }
    accepted = cursor if status in {
        KrDayCapsuleShadowStatus.REGISTERED,
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowStatus.STOPPED,
        KrDayCapsuleShadowStatus.TARGETED,
    } else (CURSOR if previous_event_id is not None else None)
    payload = KrDayCapsuleShadowEventPayload(
        capsule_id="a" * 64,
        evaluation_id=("c" if cursor == CURSOR else "d") * 64,
        session_date=SESSION,
        calendar_snapshot_id="calendar-1",
        collection_cycle_id="cycle-1",
        symbol="005930",
        attempted_bar_cursor=cursor,
        accepted_bar_cursor=accepted,
        previous_event_id=previous_event_id,
        status=status,
        reason=reason,
        signal_id="signal-1" if has_position else None,
        entry_price=Decimal("101") if has_position else None,
        stop_price=Decimal("99") if has_position else None,
        target_prices=(Decimal("103"),) if has_position else (),
        occurred_at=cursor + dt.timedelta(seconds=2),
        evaluation_payload_sha256="e" * 64,
        bar_payload_sha256="f" * 64,
    )
    return KrDayCapsuleShadowEvent(
        event_id=KrDayCapsuleShadowEvent.canonical_id_for(payload),
        **payload.model_dump(mode="python"),
    )
