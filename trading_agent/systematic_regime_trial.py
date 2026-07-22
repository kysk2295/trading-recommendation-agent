from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from pydantic import ValidationError

from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    StrategyLifecycleState,
    TrialEventKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_models import SystematicRecommendationCard
from trading_agent.systematic_regime_research import (
    ensure_systematic_regime_research,
    systematic_regime_strategy_version,
)
from trading_agent.systematic_regime_store import (
    SystematicRegimeStore,
    SystematicShadowOutcome,
)
from trading_agent.systematic_regime_trial_artifacts import (
    build_systematic_lifecycle_event,
    build_systematic_shadow_outcome,
    build_systematic_trial_registration,
)
from trading_agent.us_equity_calendar import regular_session_bounds


class InvalidSystematicRegimeTrialError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime shadow trial is invalid"


@dataclass(frozen=True, slots=True)
class SystematicTrialRegistrationResult:
    created: bool
    registration: MultiMarketExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class SystematicTrialEventResult:
    created: bool
    event: ExperimentTrialEvent


@dataclass(frozen=True, slots=True)
class SystematicTrialFinalizeResult:
    created: bool
    event: ExperimentTrialEvent
    outcome: SystematicShadowOutcome


def register_systematic_regime_trial(
    ledger: ExperimentLedgerStore,
    card: SystematicRecommendationCard,
    code_version: str,
) -> SystematicTrialRegistrationResult:
    try:
        checked = SystematicRecommendationCard.model_validate(card.model_dump(mode="python"))
        research = ensure_systematic_regime_research(ledger, code_version, checked.observed_at)
        if checked.strategy_version != systematic_regime_strategy_version(code_version):
            raise InvalidSystematicRegimeTrialError
        bounds = regular_session_bounds(checked.target_session)
        if bounds is None or checked.observed_at >= bounds[0]:
            raise InvalidSystematicRegimeTrialError
        registration = build_systematic_trial_registration(
            checked,
            research.hypothesis.experiment_scope,
        )
        lifecycle = build_systematic_lifecycle_event(checked, research)
        existing_lifecycle = ledger.multi_market_lifecycle_events(checked.strategy_version)
        if existing_lifecycle and (
            len(existing_lifecycle) != 1
            or existing_lifecycle[0].event.strategy_lane != research.version.strategy_lane
            or existing_lifecycle[0].event.to_state is not StrategyLifecycleState.EXPERIMENTAL_SHADOW
        ):
            raise InvalidSystematicRegimeTrialError
        with ledger.writer() as writer:
            if not existing_lifecycle:
                _ = writer.append_multi_market_lifecycle_event(lifecycle)
            created = writer.register_multi_market_trial(registration)
        return SystematicTrialRegistrationResult(created, registration)
    except InvalidSystematicRegimeTrialError:
        raise
    except (AttributeError, RuntimeError, ValidationError, ValueError):
        raise InvalidSystematicRegimeTrialError from None


def start_systematic_regime_trial(
    ledger: ExperimentLedgerStore,
    card: SystematicRecommendationCard,
    started_at: dt.datetime,
) -> SystematicTrialEventResult:
    try:
        registration = _require_trial(ledger, card)
        bounds = regular_session_bounds(card.target_session)
        if (
            bounds is None
            or started_at.tzinfo is None
            or started_at.utcoffset() is None
            or not bounds[0] <= started_at < bounds[1]
        ):
            raise InvalidSystematicRegimeTrialError
        events = ledger.multi_market_trial_events(registration.trial_id)
        if events:
            if len(events) != 1 or events[0].event.event_kind is not TrialEventKind.STARTED:
                raise InvalidSystematicRegimeTrialError
            return SystematicTrialEventResult(False, events[0].event)
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=1,
            event_kind=TrialEventKind.STARTED,
            occurred_at=started_at,
            artifact_sha256s=(),
            reason_codes=(),
            previous_event_key=None,
        )
        with ledger.writer() as writer:
            created = writer.append_multi_market_trial_event(event)
        return SystematicTrialEventResult(created, event)
    except InvalidSystematicRegimeTrialError:
        raise
    except (AttributeError, RuntimeError, ValidationError, ValueError):
        raise InvalidSystematicRegimeTrialError from None


def finalize_systematic_regime_trial(
    ledger: ExperimentLedgerStore,
    store: SystematicRegimeStore,
    card: SystematicRecommendationCard,
    source: SwingDailySource,
) -> SystematicTrialFinalizeResult:
    try:
        registration = _require_trial(ledger, card)
        checked_source = SwingDailySource.model_validate(source.model_dump(mode="python"))
        bounds = regular_session_bounds(card.target_session)
        cards = tuple(item for item in store.cards() if item.card_id == card.card_id)
        stored_card = next(iter(cards), None)
        if (
            len(cards) != 1
            or stored_card != card
            or checked_source.session_date != card.target_session
            or bounds is None
            or checked_source.observed_at < bounds[1]
        ):
            raise InvalidSystematicRegimeTrialError
        events = ledger.multi_market_trial_events(registration.trial_id)
        started = next(iter(events), None)
        if started is None or len(events) > 2 or started.event.event_kind is not TrialEventKind.STARTED:
            raise InvalidSystematicRegimeTrialError
        outcome = build_systematic_shadow_outcome(card, checked_source)
        with store.writer() as writer:
            _ = writer.append_outcome(outcome)
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=2,
            event_kind=TrialEventKind.COMPLETED,
            occurred_at=checked_source.observed_at,
            artifact_sha256s=tuple(
                sorted((card.artifact_sha256, checked_source.source_key, outcome.artifact_sha256))
            ),
            reason_codes=(),
            previous_event_key=str(started.event_key),
        )
        terminal = next(iter(events[1:]), None)
        if terminal is not None:
            if terminal.event != event:
                raise InvalidSystematicRegimeTrialError
            return SystematicTrialFinalizeResult(False, event, outcome)
        with ledger.writer() as writer:
            created = writer.append_multi_market_trial_event(event)
        return SystematicTrialFinalizeResult(created, event, outcome)
    except InvalidSystematicRegimeTrialError:
        raise
    except (AttributeError, RuntimeError, ValidationError, ValueError):
        raise InvalidSystematicRegimeTrialError from None


def _require_trial(
    ledger: ExperimentLedgerStore,
    card: SystematicRecommendationCard,
) -> MultiMarketExperimentTrialRegistration:
    matches = tuple(
        item.registration
        for item in ledger.multi_market_trials()
        if item.registration.trial_id.startswith(f"us-systematic-regime-{card.target_session:%Y%m%d}-")
    )
    if len(matches) != 1 or matches[0].strategy_version != card.strategy_version:
        raise InvalidSystematicRegimeTrialError
    return matches[0]


__all__ = (
    "InvalidSystematicRegimeTrialError",
    "SystematicTrialEventResult",
    "SystematicTrialFinalizeResult",
    "SystematicTrialRegistrationResult",
    "finalize_systematic_regime_trial",
    "register_systematic_regime_trial",
    "start_systematic_regime_trial",
)
