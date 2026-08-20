from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from trading_agent.day_forward_trial_models import (
    DayForwardTrial,
    DayForwardTrialEvent,
    DayForwardTrialEventKind,
)


@dataclass(frozen=True, slots=True)
class DayForwardTrialLedgerConflictError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class InvalidDayForwardTrialLedgerSourceError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def require_event_parent(
    trial: DayForwardTrial,
    event: DayForwardTrialEvent,
) -> None:
    if (
        event.trial_id != trial.trial_id
        or event.market_id is not trial.market_id
        or event.session_id != trial.session_id
        or event.session_date != trial.session_date
    ):
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_event_parent_mismatch")
    if event.completed_bar_at < trial.first_eligible_completed_bar_at:
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_event_not_future")


def require_event_chain(
    trial: DayForwardTrial,
    events: tuple[DayForwardTrialEvent, ...],
) -> None:
    previous: DayForwardTrialEvent | None = None
    entered = False
    terminal = False
    for expected_sequence, event in enumerate(events, start=1):
        require_event_parent(trial, event)
        if (
            terminal
            or event.sequence != expected_sequence
            or event.previous_event_id != (None if previous is None else previous.event_id)
            or (previous is not None and event.event_at < previous.event_at)
            or (previous is not None and event.completed_bar_at < previous.completed_bar_at)
            or (previous is not None and event.completed_bar_sequence < previous.completed_bar_sequence)
            or _same_sequence_bar_changed(previous, event)
        ):
            raise InvalidDayForwardTrialLedgerSourceError("forward_trial_event_chain_invalid")
        if previous is not None and event.completed_bar_sequence > previous.completed_bar_sequence + 1 and (
            event.event_kind is not DayForwardTrialEventKind.CENSORED
            or event.reason_codes != ("completed_bar_gap",)
        ):
            raise InvalidDayForwardTrialLedgerSourceError("forward_trial_event_gap_invalid")
        match event.event_kind:
            case DayForwardTrialEventKind.ENTRY:
                if previous is None or previous.event_kind is not DayForwardTrialEventKind.SIGNAL or (
                    previous.completed_bar_id != event.completed_bar_id
                ):
                    raise InvalidDayForwardTrialLedgerSourceError("forward_trial_entry_without_signal")
                entered = True
            case DayForwardTrialEventKind.OBSERVED:
                if not entered:
                    raise InvalidDayForwardTrialLedgerSourceError(
                        "forward_trial_observed_without_entry"
                    )
            case DayForwardTrialEventKind.EXIT:
                if not entered:
                    raise InvalidDayForwardTrialLedgerSourceError("forward_trial_exit_without_entry")
                terminal = True
            case DayForwardTrialEventKind.FAILED | DayForwardTrialEventKind.CENSORED:
                terminal = True
            case DayForwardTrialEventKind.SIGNAL | DayForwardTrialEventKind.NO_SIGNAL:
                if entered:
                    raise InvalidDayForwardTrialLedgerSourceError("forward_trial_signal_after_entry")
            case DayForwardTrialEventKind.BLOCKED:
                pass
            case unreachable:
                assert_never(unreachable)
        previous = event


def event_replay(
    events: tuple[DayForwardTrialEvent, ...],
    proposed: DayForwardTrialEvent,
) -> DayForwardTrialEvent | None:
    same_identity = next(
        (
            event
            for event in events
            if event.completed_bar_id == proposed.completed_bar_id
            and event.event_kind is proposed.event_kind
        ),
        None,
    )
    if same_identity is not None:
        if same_identity == proposed:
            return same_identity
        raise DayForwardTrialLedgerConflictError("forward_trial_event_identity_conflict")
    gap_replay = next(
        (
            event
            for event in events
            if event.completed_bar_id == proposed.completed_bar_id
            and event.event_kind is DayForwardTrialEventKind.CENSORED
            and event.reason_codes == ("completed_bar_gap",)
        ),
        None,
    )
    if gap_replay is not None:
        return gap_replay
    if any(event.sequence == proposed.sequence or event.event_id == proposed.event_id for event in events):
        raise DayForwardTrialLedgerConflictError("forward_trial_event_identity_conflict")
    return None


def gap_censor(event: DayForwardTrialEvent) -> DayForwardTrialEvent:
    payload = event.model_dump(mode="python") | {
        "event_id": "",
        "event_kind": DayForwardTrialEventKind.CENSORED,
        "exit_reason": None,
        "outcome_ref": None,
        "reason_codes": ("completed_bar_gap",),
    }
    return DayForwardTrialEvent.model_validate(
        payload | {"event_id": DayForwardTrialEvent.canonical_id_for(payload)}
    )


def validated_event(event: DayForwardTrialEvent) -> DayForwardTrialEvent:
    try:
        return DayForwardTrialEvent.model_validate(event.model_dump(mode="python"))
    except ValueError:
        raise InvalidDayForwardTrialLedgerSourceError("forward_trial_event_invalid") from None


def _same_sequence_bar_changed(
    previous: DayForwardTrialEvent | None,
    event: DayForwardTrialEvent,
) -> bool:
    return (
        previous is not None
        and event.completed_bar_sequence == previous.completed_bar_sequence
        and (
            event.completed_bar_id != previous.completed_bar_id
            or event.completed_bar_at != previous.completed_bar_at
        )
    )


__all__ = (
    "DayForwardTrialLedgerConflictError",
    "InvalidDayForwardTrialLedgerSourceError",
    "event_replay",
    "gap_censor",
    "require_event_chain",
    "require_event_parent",
    "validated_event",
)
