from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Final, override

from trading_agent.experiment_ledger_models import TrialEventKind, TrialKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.signal_contract_models import TradeSignalEnvelope
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
from trading_agent.swing_shadow_review_models import (
    CURRENT_SWING_SHADOW_REVIEWER_VERSION,
    SwingShadowReviewerAction,
    SwingShadowReviewEvent,
)
from trading_agent.swing_shadow_review_store import (
    InvalidSwingShadowReviewSourceError,
    SwingShadowReviewConflictError,
    SwingShadowReviewStore,
)
from trading_agent.swing_shadow_store import SwingShadowReader
from trading_agent.swing_shadow_trial import (
    InvalidSwingShadowTrialSourceError,
    swing_shadow_trial_artifact_sha256s,
    swing_shadow_trial_data_version,
    swing_shadow_trial_id,
)

SWING_SHADOW_REVIEWER_VERSION: Final = CURRENT_SWING_SHADOW_REVIEWER_VERSION
_BLOCKERS: Final = (
    "automatic_state_change_forbidden",
    "cost_model_unmodeled",
    "forward_sample_insufficient",
    "paper_authority_forbidden",
)


class InvalidSwingShadowReviewError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow Reviewer가 exact 완료 증거를 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class SwingShadowReviewResult:
    created: bool
    event: SwingShadowReviewEvent


def review_swing_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerReader,
    shadow_ledger: SwingShadowReader,
    reviews: SwingShadowReviewStore,
    signal_id: str,
    reviewed_at: dt.datetime,
) -> SwingShadowReviewResult:
    try:
        event = _build_review_event(
            experiment_ledger=experiment_ledger,
            shadow_ledger=shadow_ledger,
            reviews=reviews,
            signal_id=signal_id,
            reviewed_at=reviewed_at,
        )
    except (
        InvalidSwingShadowReviewError,
        InvalidSwingShadowReviewSourceError,
        InvalidSwingShadowTrialSourceError,
        OSError,
        sqlite3.Error,
        ValueError,
    ):
        raise InvalidSwingShadowReviewError from None
    try:
        with reviews.writer() as writer:
            created = writer.append_event(event)
    except (
        InvalidSwingShadowReviewSourceError,
        SwingShadowReviewConflictError,
        OSError,
        sqlite3.Error,
        ValueError,
    ):
        raise InvalidSwingShadowReviewError from None
    return SwingShadowReviewResult(created, event)


def _build_review_event(
    *,
    experiment_ledger: ExperimentLedgerReader,
    shadow_ledger: SwingShadowReader,
    reviews: SwingShadowReviewStore,
    signal_id: str,
    reviewed_at: dt.datetime,
) -> SwingShadowReviewEvent:
    if not _aware(reviewed_at):
        raise InvalidSwingShadowReviewError
    signal = _signal_by_id(shadow_ledger, signal_id)
    shadow_events = shadow_ledger.events(signal_id)
    created = next(iter(shadow_events), None)
    if created is None:
        raise InvalidSwingShadowReviewError
    data_version = swing_shadow_trial_data_version(signal, created)
    artifacts = swing_shadow_trial_artifact_sha256s(signal, shadow_events)
    terminal = shadow_events[-1]
    trial_id = swing_shadow_trial_id(signal)
    trials = tuple(stored for stored in experiment_ledger.trials() if stored.registration.trial_id == trial_id)
    if len(trials) != 1:
        raise InvalidSwingShadowReviewError
    registration = trials[0].registration
    if (
        registration.strategy_version != SWING_RESEARCH_CONTRACT.strategy_version
        or registration.trial_kind is not TrialKind.SHADOW_FORWARD
        or registration.experiment_scope != SWING_RESEARCH_CONTRACT.experiment_scope
        or registration.experiment_scope_key != experiment_scope_key(SWING_RESEARCH_CONTRACT.experiment_scope)
        or registration.data_version != data_version
        or terminal.session_date < registration.planned_start
        or terminal.session_date > registration.planned_end
    ):
        raise InvalidSwingShadowReviewError
    events = experiment_ledger.trial_events(registration.trial_id)
    if (
        len(events) != 2
        or events[0].event.event_kind is not TrialEventKind.STARTED
        or events[1].event.event_kind is not TrialEventKind.COMPLETED
        or events[1].event.artifact_sha256s != artifacts
        or reviewed_at < terminal.observed_at
        or reviewed_at < events[1].event.occurred_at
    ):
        raise InvalidSwingShadowReviewError
    existing = reviews.review_event(signal_id, SWING_SHADOW_REVIEWER_VERSION)
    event_reviewed_at = reviewed_at if existing is None else existing.event.reviewed_at
    return SwingShadowReviewEvent(
        signal_id=signal.signal_id,
        trial_id=registration.trial_id,
        strategy_version=registration.strategy_version,
        experiment_scope_key=registration.experiment_scope_key,
        terminal_event_key=events[1].event_key,
        artifact_sha256s=artifacts,
        terminal_kind=terminal.kind,
        reviewer_version=SWING_SHADOW_REVIEWER_VERSION,
        reviewer_action=SwingShadowReviewerAction.CONTINUE_COLLECTION,
        reasons=(f"terminal_{terminal.kind.value}",),
        blockers=_BLOCKERS,
        reviewed_at=event_reviewed_at,
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
        allocation_change_allowed=False,
    )


def _signal_by_id(shadow_ledger: SwingShadowReader, signal_id: str) -> TradeSignalEnvelope:
    signals = tuple(signal for signal in shadow_ledger.signals() if signal.signal_id == signal_id)
    if len(signals) != 1:
        raise InvalidSwingShadowReviewError
    return signals[0]


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "SWING_SHADOW_REVIEWER_VERSION",
    "InvalidSwingShadowReviewError",
    "SwingShadowReviewResult",
    "review_swing_shadow_trial",
)
