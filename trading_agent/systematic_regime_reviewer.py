from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Final, override

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_models import SystematicRecommendationCard
from trading_agent.systematic_regime_review_models import (
    CURRENT_SYSTEMATIC_REGIME_REVIEWER_VERSION,
    SystematicRegimeReviewerAction,
    SystematicRegimeReviewEvent,
)
from trading_agent.systematic_regime_review_store import (
    InvalidSystematicRegimeReviewSourceError,
    SystematicRegimeReviewConflictError,
    SystematicRegimeReviewStore,
)
from trading_agent.systematic_regime_store import SystematicRegimeStore
from trading_agent.systematic_regime_trial_artifacts import (
    build_systematic_shadow_outcome,
    build_systematic_trial_registration,
)

SYSTEMATIC_REGIME_REVIEWER_VERSION: Final = CURRENT_SYSTEMATIC_REGIME_REVIEWER_VERSION
_BLOCKERS: Final = (
    "allocation_manager_forbidden",
    "automatic_state_change_forbidden",
    "executable_paper_champions:0/2",
    "forward_sample_insufficient",
    "paper_authority_forbidden",
)


class InvalidSystematicRegimeReviewError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime Reviewer could not verify exact terminal evidence"


@dataclass(frozen=True, slots=True)
class SystematicRegimeReviewResult:
    created: bool
    event: SystematicRegimeReviewEvent


def review_systematic_regime_trial(
    *,
    experiment_ledger: ExperimentLedgerReader,
    systematic_store: SystematicRegimeStore,
    daily_sources: tuple[SwingDailySource, ...],
    reviews: SystematicRegimeReviewStore,
    card_id: str,
    reviewed_at: dt.datetime,
) -> SystematicRegimeReviewResult:
    try:
        event = _build_review_event(
            experiment_ledger=experiment_ledger,
            systematic_store=systematic_store,
            daily_sources=daily_sources,
            reviews=reviews,
            card_id=card_id,
            reviewed_at=reviewed_at,
        )
    except (
        InvalidSystematicRegimeReviewError,
        InvalidSystematicRegimeReviewSourceError,
        OSError,
        sqlite3.Error,
        ValueError,
    ):
        raise InvalidSystematicRegimeReviewError from None
    try:
        with reviews.writer() as writer:
            created = writer.append_event(event)
    except (
        InvalidSystematicRegimeReviewSourceError,
        SystematicRegimeReviewConflictError,
        OSError,
        sqlite3.Error,
        ValueError,
    ):
        raise InvalidSystematicRegimeReviewError from None
    return SystematicRegimeReviewResult(created, event)


def _build_review_event(
    *,
    experiment_ledger: ExperimentLedgerReader,
    systematic_store: SystematicRegimeStore,
    daily_sources: tuple[SwingDailySource, ...],
    reviews: SystematicRegimeReviewStore,
    card_id: str,
    reviewed_at: dt.datetime,
) -> SystematicRegimeReviewEvent:
    if not _aware(reviewed_at):
        raise InvalidSystematicRegimeReviewError
    card, expired = _card_by_id(systematic_store, card_id)
    registrations = tuple(
        stored.registration
        for stored in experiment_ledger.multi_market_trials()
        if stored.registration.trial_id.startswith(
            f"us-systematic-regime-{card.target_session:%Y%m%d}-"
        )
        and stored.registration.strategy_version == card.strategy_version
    )
    if len(registrations) != 1:
        raise InvalidSystematicRegimeReviewError
    registration = registrations[0]
    expected_registration = build_systematic_trial_registration(card, registration.experiment_scope)
    if registration != expected_registration:
        raise InvalidSystematicRegimeReviewError
    events = experiment_ledger.multi_market_trial_events(registration.trial_id)
    terminal = next(iter(events[-1:]), None)
    if (
        terminal is None
        or terminal.event.event_kind not in (TrialEventKind.COMPLETED, TrialEventKind.CENSORED)
        or reviewed_at < terminal.event.occurred_at
    ):
        raise InvalidSystematicRegimeReviewError
    outcome_hash: str | None
    if terminal.event.event_kind is TrialEventKind.COMPLETED:
        if expired or len(events) != 2 or events[0].event.event_kind is not TrialEventKind.STARTED:
            raise InvalidSystematicRegimeReviewError
        outcomes = tuple(
            outcome for outcome in systematic_store.outcomes() if outcome.card_id == card.card_id
        )
        sources = tuple(
            source
            for source in daily_sources
            if source.session_date == card.target_session
            and source.source_key in terminal.event.artifact_sha256s
        )
        if len(outcomes) != 1 or len(sources) != 1:
            raise InvalidSystematicRegimeReviewError
        outcome = outcomes[0]
        source = sources[0]
        if outcome != build_systematic_shadow_outcome(card, source):
            raise InvalidSystematicRegimeReviewError
        expected_artifacts = tuple(
            sorted((card.artifact_sha256, source.source_key, outcome.artifact_sha256))
        )
        if terminal.event.artifact_sha256s != expected_artifacts:
            raise InvalidSystematicRegimeReviewError
        outcome_hash = outcome.artifact_sha256
        action = SystematicRegimeReviewerAction.CONTINUE_COLLECTION
        reason = "completed_no_position" if outcome.no_position else "completed_shadow_position"
    else:
        if (
            not expired
            or len(events) != 1
            or terminal.event.reason_codes != ("missed_target_session_operating_tick",)
            or any(
                outcome.card_id == card.card_id
                for outcome in systematic_store.outcomes()
            )
        ):
            raise InvalidSystematicRegimeReviewError
        outcome_hash = None
        action = SystematicRegimeReviewerAction.DATA_QUALITY_REVIEW
        reason = "censored_missed_target_session"
    existing = reviews.review_event(card.card_id, SYSTEMATIC_REGIME_REVIEWER_VERSION)
    event_reviewed_at = reviewed_at if existing is None else existing.event.reviewed_at
    return SystematicRegimeReviewEvent(
        card_id=card.card_id,
        trial_id=registration.trial_id,
        strategy_version=registration.strategy_version,
        experiment_scope_key=registration.experiment_scope_key,
        terminal_event_key=str(terminal.event_key),
        terminal_kind=terminal.event.event_kind,
        artifact_sha256s=terminal.event.artifact_sha256s,
        outcome_artifact_sha256=outcome_hash,
        reviewer_version=SYSTEMATIC_REGIME_REVIEWER_VERSION,
        reviewer_action=action,
        reasons=(reason,),
        blockers=_BLOCKERS,
        reviewed_at=event_reviewed_at,
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
        allocation_change_allowed=False,
    )


def _card_by_id(
    systematic_store: SystematicRegimeStore,
    card_id: str,
) -> tuple[SystematicRecommendationCard, bool]:
    current = tuple(card for card in systematic_store.cards() if card.card_id == card_id)
    expired = tuple(card for card in systematic_store.expired_cards() if card.card_id == card_id)
    if len(current) + len(expired) != 1:
        raise InvalidSystematicRegimeReviewError
    return (current[0], False) if current else (expired[0], True)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "SYSTEMATIC_REGIME_REVIEWER_VERSION",
    "InvalidSystematicRegimeReviewError",
    "SystematicRegimeReviewResult",
    "review_systematic_regime_trial",
)
