from __future__ import annotations

import datetime as dt
from typing import Final, assert_never

from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
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

_TERMINAL_KINDS: Final = frozenset(
    {
        ShadowEventKind.EXPIRED,
        ShadowEventKind.STOPPED,
        ShadowEventKind.TARGETED,
        ShadowEventKind.TIME_EXIT,
    }
)
_SWING_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.SWING_TRADING,
    strategy_id=SWING_RESEARCH_CONTRACT.strategy_id,
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
    session_date = now.date()
    phase = _phase(now)
    scanner_executed = False
    operation_request = request
    match phase:
        case SwingOperatingPhase.NON_SESSION:
            return SwingOperatingResult(phase, False, 0, 0, 0, 0, 0, ())
        case SwingOperatingPhase.POST_CLOSE if not _has_source_cycle(config.delivery_store, session_date):
            scanned_at = config.scanner.run(session_date)
            if (
                not _aware(scanned_at)
                or scanned_at < request.now
                or scanned_at.astimezone(NEW_YORK).date() != session_date
            ):
                raise InvalidSwingOperatingRequestError
            operation_request = SwingOperatingRequest(scanned_at, request.runtime_code_version)
            scanner_executed = True
        case SwingOperatingPhase.PRE_OPEN | SwingOperatingPhase.REGULAR | SwingOperatingPhase.POST_CLOSE:
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
    registrations = {
        stored.registration.trial_id: stored.registration for stored in config.experiment_ledger.trials()
    }
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
    registrations = {
        stored.registration.trial_id: stored.registration for stored in config.experiment_ledger.trials()
    }
    finalized = delivered = reviewed = 0
    blocked: list[str] = []
    for signal in config.shadow_ledger.signals():
        events = config.shadow_ledger.events(signal.signal_id)
        registration = registrations.get(swing_shadow_trial_id(signal))
        if not events or events[-1].kind not in _TERMINAL_KINDS or registration is None:
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


def _has_source_cycle(store: HermesDeliveryStore, session_date: dt.date) -> bool:
    return any(
        event.root_delivery_id == event.delivery_id
        and event.kind in {HermesDeliveryKind.WATCH, HermesDeliveryKind.NO_RECOMMENDATION}
        and event.occurred_at.astimezone(NEW_YORK).date() == session_date
        and event.market_id == _SWING_LANE.market_id.value
        and event.agent_family == _SWING_LANE.agent_family.value
        and event.lane_id == _SWING_LANE.canonical_id
        and event.strategy_version == SWING_RESEARCH_CONTRACT.strategy_version
        for event in store.events()
    )


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
