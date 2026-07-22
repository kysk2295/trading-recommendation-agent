from __future__ import annotations

import datetime as dt
from typing import Final, assert_never

from trading_agent.swing_shadow_delivery import project_swing_shadow_terminal_delivery
from trading_agent.swing_shadow_reviewer import review_swing_shadow_trial
from trading_agent.swing_shadow_store import ShadowEventKind
from trading_agent.swing_shadow_trial import (
    finalize_swing_shadow_trial,
    register_swing_shadow_trial,
    start_swing_shadow_trial,
    swing_shadow_trial_id,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_swing_operating_models import (
    InvalidSwingOperatingRequestError,
    SwingDailyScanner,
    SwingOperatingConfig,
    SwingOperatingPhase,
    SwingOperatingRequest,
    SwingOperatingResult,
)
from trading_agent.us_swing_source_cycle import run_post_close_swing_source_cycle

_TERMINAL_KINDS: Final = frozenset(
    {
        ShadowEventKind.EXPIRED,
        ShadowEventKind.STOPPED,
        ShadowEventKind.TARGETED,
        ShadowEventKind.TIME_EXIT,
    }
)


def run_us_swing_operating_tick(
    request: SwingOperatingRequest,
    config: SwingOperatingConfig,
) -> SwingOperatingResult:
    if (
        not _aware(request.now)
        or not request.runtime_code_version
        or request.runtime_code_version != request.runtime_code_version.strip()
    ):
        raise InvalidSwingOperatingRequestError
    now = request.now.astimezone(NEW_YORK)
    phase = _phase(now)
    scanner_executed = False
    incidents = 0
    operation_request = request
    match phase:
        case SwingOperatingPhase.NON_SESSION:
            return SwingOperatingResult(
                phase=phase,
                scanner_executed=False,
                registered=0,
                started=0,
                finalized=0,
                delivered=0,
                incidents=0,
                reviewed=0,
                blocked_signal_ids=(),
            )
        case SwingOperatingPhase.POST_CLOSE:
            source_cycle = run_post_close_swing_source_cycle(request, config)
            operation_request = source_cycle.operation_request
            scanner_executed = source_cycle.scanner_executed
            incidents = source_cycle.incidents
        case SwingOperatingPhase.PRE_OPEN | SwingOperatingPhase.REGULAR:
            pass
        case unreachable:
            assert_never(unreachable)

    registered, registration_blocks = _register_available(operation_request, config, phase)
    started, start_blocks = _start_due(operation_request, config, phase)
    finalized, delivered, reviewed, terminal_blocks = _reconcile_terminals(operation_request, config)
    return SwingOperatingResult(
        phase=phase,
        scanner_executed=scanner_executed,
        registered=registered,
        started=started,
        finalized=finalized,
        delivered=delivered,
        incidents=incidents,
        reviewed=reviewed,
        blocked_signal_ids=tuple(sorted(set((*registration_blocks, *start_blocks, *terminal_blocks)))),
    )


def _register_available(
    request: SwingOperatingRequest,
    config: SwingOperatingConfig,
    phase: SwingOperatingPhase,
) -> tuple[int, tuple[str, ...]]:
    trials = {stored.registration.trial_id for stored in config.experiment_ledger.trials()}
    now = request.now.astimezone(NEW_YORK)
    created = 0
    blocked: list[str] = []
    for signal in config.shadow_ledger.signals():
        if swing_shadow_trial_id(signal) in trials:
            continue
        planned_date = signal.valid_until.astimezone(NEW_YORK).date()
        bounds = regular_session_bounds(planned_date)
        if bounds is None or now >= bounds[0]:
            blocked.append(signal.signal_id)
            continue
        match phase:
            case SwingOperatingPhase.PRE_OPEN | SwingOperatingPhase.POST_CLOSE:
                result = register_swing_shadow_trial(
                    experiment_ledger=config.experiment_ledger,
                    shadow_ledger=config.shadow_ledger,
                    signal_id=signal.signal_id,
                    runtime_code_version=request.runtime_code_version,
                    registered_at=request.now,
                )
                created += int(result.created)
            case SwingOperatingPhase.REGULAR | SwingOperatingPhase.NON_SESSION:
                pass
            case unreachable:
                assert_never(unreachable)
    return created, tuple(blocked)


def _start_due(
    request: SwingOperatingRequest,
    config: SwingOperatingConfig,
    phase: SwingOperatingPhase,
) -> tuple[int, tuple[str, ...]]:
    session_date = request.now.astimezone(NEW_YORK).date()
    registrations = {stored.registration.trial_id: stored.registration for stored in config.experiment_ledger.trials()}
    started = 0
    blocked: list[str] = []
    for signal in config.shadow_ledger.signals():
        registration = registrations.get(swing_shadow_trial_id(signal))
        if registration is None or registration.planned_start > session_date:
            continue
        events = config.experiment_ledger.trial_events(registration.trial_id)
        if events:
            continue
        match phase:
            case SwingOperatingPhase.REGULAR if registration.planned_start == session_date:
                result = start_swing_shadow_trial(
                    experiment_ledger=config.experiment_ledger,
                    shadow_ledger=config.shadow_ledger,
                    signal_id=signal.signal_id,
                    started_at=request.now,
                )
                started += int(result.created)
            case SwingOperatingPhase.PRE_OPEN:
                if registration.planned_start < session_date:
                    blocked.append(signal.signal_id)
            case SwingOperatingPhase.POST_CLOSE:
                blocked.append(signal.signal_id)
            case SwingOperatingPhase.REGULAR | SwingOperatingPhase.NON_SESSION:
                blocked.append(signal.signal_id)
            case unreachable:
                assert_never(unreachable)
    return started, tuple(blocked)


def _reconcile_terminals(
    request: SwingOperatingRequest,
    config: SwingOperatingConfig,
) -> tuple[int, int, int, tuple[str, ...]]:
    registrations = {stored.registration.trial_id: stored.registration for stored in config.experiment_ledger.trials()}
    finalized = delivered = reviewed = 0
    blocked: list[str] = []
    for signal in config.shadow_ledger.signals():
        events = config.shadow_ledger.events(signal.signal_id)
        registration = registrations.get(swing_shadow_trial_id(signal))
        if not events or events[-1].kind not in _TERMINAL_KINDS or registration is None:
            continue
        if events[-1].observed_at > request.now:
            continue
        trial_events = config.experiment_ledger.trial_events(registration.trial_id)
        if not trial_events:
            blocked.append(signal.signal_id)
            continue
        completed = finalize_swing_shadow_trial(
            experiment_ledger=config.experiment_ledger,
            shadow_ledger=config.shadow_ledger,
            signal_id=signal.signal_id,
            finalized_at=request.now,
        )
        finalized += int(completed.created)
        projection = project_swing_shadow_terminal_delivery(
            config.experiment_ledger,
            config.shadow_ledger,
            config.delivery_store,
            signal.signal_id,
        )
        delivered += projection.inserted
        review = review_swing_shadow_trial(
            experiment_ledger=config.experiment_ledger,
            shadow_ledger=config.shadow_ledger,
            reviews=config.review_store,
            signal_id=signal.signal_id,
            reviewed_at=request.now,
        )
        reviewed += int(review.created)
    return finalized, delivered, reviewed, tuple(blocked)


def _phase(now: dt.datetime) -> SwingOperatingPhase:
    bounds = regular_session_bounds(now.date())
    if bounds is None:
        return SwingOperatingPhase.NON_SESSION
    if now < bounds[0]:
        return SwingOperatingPhase.PRE_OPEN
    if now < bounds[1]:
        return SwingOperatingPhase.REGULAR
    return SwingOperatingPhase.POST_CLOSE


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidSwingOperatingRequestError",
    "SwingDailyScanner",
    "SwingOperatingConfig",
    "SwingOperatingPhase",
    "SwingOperatingRequest",
    "SwingOperatingResult",
    "run_us_swing_operating_tick",
)
