from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import override

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_engine import build_systematic_card, replay_systematic_regime
from trading_agent.systematic_regime_research import systematic_regime_strategy_version
from trading_agent.systematic_regime_store import SystematicRegimeStore
from trading_agent.systematic_regime_trial import (
    finalize_systematic_regime_trial,
    register_systematic_regime_trial,
    start_systematic_regime_trial,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class SystematicOperatingPhase(StrEnum):
    PRE_OPEN = "pre_open"
    REGULAR_SESSION = "regular_session"
    POST_CLOSE = "post_close"


class InvalidSystematicOperatingTickError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime operating tick is invalid"


@dataclass(frozen=True, slots=True)
class SystematicOperatingResult:
    phase: SystematicOperatingPhase
    cards_created: int
    trials_registered: int
    trials_started: int
    trials_finalized: int


def run_systematic_regime_tick(
    *,
    now: dt.datetime,
    code_version: str,
    experiment_ledger: ExperimentLedgerStore,
    store: SystematicRegimeStore,
    source: SwingDailySource | None,
) -> SystematicOperatingResult:
    phase = systematic_operating_phase(now)
    if phase is SystematicOperatingPhase.PRE_OPEN:
        if source is not None:
            raise InvalidSystematicOperatingTickError
        return SystematicOperatingResult(phase, 0, 0, 0, 0)
    if phase is SystematicOperatingPhase.REGULAR_SESSION:
        if source is not None:
            raise InvalidSystematicOperatingTickError
        started = sum(
            start_systematic_regime_trial(experiment_ledger, card, now).created
            for card in store.cards()
            if card.target_session == now.astimezone(NEW_YORK).date()
        )
        return SystematicOperatingResult(phase, 0, 0, started, 0)
    if source is None or source.session_date != now.astimezone(NEW_YORK).date():
        raise InvalidSystematicOperatingTickError
    finalized = sum(
        finalize_systematic_regime_trial(experiment_ledger, store, card, source).created
        for card in store.cards()
        if card.target_session == source.session_date
    )
    version = systematic_regime_strategy_version(code_version)
    card = build_systematic_card(source, replay_systematic_regime(source), version)
    with store.writer() as writer:
        card_created = int(writer.append_card(card))
    trial_created = int(register_systematic_regime_trial(experiment_ledger, card, code_version).created)
    return SystematicOperatingResult(phase, card_created, trial_created, 0, finalized)


def systematic_operating_phase(now: dt.datetime) -> SystematicOperatingPhase:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidSystematicOperatingTickError
    local = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    if bounds is None:
        raise InvalidSystematicOperatingTickError
    if local < bounds[0]:
        return SystematicOperatingPhase.PRE_OPEN
    if local < bounds[1]:
        return SystematicOperatingPhase.REGULAR_SESSION
    return SystematicOperatingPhase.POST_CLOSE


__all__ = (
    "InvalidSystematicOperatingTickError",
    "SystematicOperatingPhase",
    "SystematicOperatingResult",
    "run_systematic_regime_tick",
    "systematic_operating_phase",
)
