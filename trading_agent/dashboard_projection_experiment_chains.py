from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from trading_agent.dashboard_reviewer_lifecycle import ReviewerLifecycleAuthorityReader
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    StoredExperimentTrialRegistration,
    StoredResearchHypothesisCard,
    StoredResearchSource,
    StoredStrategyVersionRegistration,
)
from trading_agent.lane_review_store import LaneReviewReader, StoredLaneReviewEvent


@dataclass(frozen=True, slots=True)
class ExperimentChain:
    source_ref: str | None
    hypothesis_ref: str | None
    dataset_sha: str | None
    code_ref: str | None
    code_version: str
    trial_ref: str | None
    terminal_ref: str | None
    reviewer_ref: str | None
    lifecycle_ref: str | None
    label: str
    value: str
    observed_at: dt.datetime
    blocker: str | None
    source_at: dt.datetime | None = None
    hypothesis_at: dt.datetime | None = None
    code_at: dt.datetime | None = None
    trial_at: dt.datetime | None = None
    trial_started_at: dt.datetime | None = None
    terminal_at: dt.datetime | None = None
    reviewed_at: dt.datetime | None = None
    lifecycle_at: dt.datetime | None = None


def read_experiment_chains(
    reader: ExperimentLedgerReader,
    reviews: LaneReviewReader,
    *,
    strategies: bool,
) -> tuple[tuple[ExperimentChain, ...], bool]:
    sources: dict[str, StoredResearchSource] = {str(stored.source_key): stored for stored in reader.research_sources()}
    cards = {stored.card.hypothesis.hypothesis_id: stored for stored in reader.research_hypothesis_cards()}
    trials = {stored.registration.strategy_version: stored for stored in reader.trials()}
    review_events = reviews.events()
    versions = reader.strategy_versions()
    version_hypotheses = {version.registration.hypothesis_id for version in versions}
    chains = [
        _chain(
            reader,
            sources,
            cards.get(version.registration.hypothesis_id),
            trials.get(version.registration.strategy_version),
            review_events,
            version,
            version.registration.lane_id.value if strategies else version.registration.hypothesis_id,
            version.registration.strategy_version if strategies else version.registration.hypothesis_id,
        )
        for version in versions
    ]
    for card in cards.values():
        if card.card.hypothesis.hypothesis_id not in version_hypotheses:
            chains.append(_unversioned_card(sources, card))
    card_source_refs = {source_ref for card in cards.values() for source_ref in card.card.research_source_keys}
    for source in sources.values():
        if str(source.source_key) not in card_source_refs:
            chains.append(_source_without_card(source))
    authority = ReviewerLifecycleAuthorityReader(experiments=(reader,), reviews=(reviews,))
    return tuple(chains), authority.allocation_manager_is_available()


def _unversioned_card(sources: dict[str, StoredResearchSource], card: StoredResearchHypothesisCard) -> ExperimentChain:
    source = sources.get(card.card.research_source_keys[0])
    return ExperimentChain(
        source_ref=str(source.source_key) if source is not None else None,
        hypothesis_ref=str(card.card_key),
        dataset_sha=None,
        code_ref=None,
        code_version="unregistered",
        trial_ref=None,
        terminal_ref=None,
        reviewer_ref=None,
        lifecycle_ref=None,
        label=card.card.hypothesis.hypothesis_id,
        value=card.card.hypothesis.hypothesis_id,
        observed_at=card.card.hypothesis.ledger_recorded_at,
        blocker="source_missing" if source is None else "dataset_sha_missing",
    )


def _source_without_card(source: StoredResearchSource) -> ExperimentChain:
    return ExperimentChain(
        source_ref=str(source.source_key),
        hypothesis_ref=None,
        dataset_sha=None,
        code_ref=None,
        code_version="unregistered",
        trial_ref=None,
        terminal_ref=None,
        reviewer_ref=None,
        lifecycle_ref=None,
        label=source.source.title,
        value=source.source.source_id,
        observed_at=source.source.ledger_recorded_at,
        blocker="source_card_missing",
    )


def _chain(
    reader: ExperimentLedgerReader,
    sources: dict[str, StoredResearchSource],
    card: StoredResearchHypothesisCard | None,
    trial: StoredExperimentTrialRegistration | None,
    review_events: tuple[StoredLaneReviewEvent, ...],
    version: StoredStrategyVersionRegistration,
    label: str,
    value: str,
) -> ExperimentChain:
    registration = version.registration
    base = {
        "source_ref": None,
        "hypothesis_ref": None,
        "dataset_sha": None,
        "code_ref": _safe_ref(registration.code_version),
        "code_version": registration.code_version,
        "trial_ref": None,
        "terminal_ref": None,
        "reviewer_ref": None,
        "lifecycle_ref": None,
        "label": label,
        "value": value,
        "observed_at": registration.ledger_recorded_at,
        "source_at": None,
        "hypothesis_at": None,
        "code_at": registration.ledger_recorded_at,
        "trial_at": None,
        "trial_started_at": None,
        "terminal_at": None,
        "reviewed_at": None,
        "lifecycle_at": None,
    }
    if card is None:
        return ExperimentChain(**base, blocker="source_card_missing")
    source = sources.get(card.card.research_source_keys[0])
    if source is None:
        return ExperimentChain(**base, blocker="source_missing")
    base = base | {
        "source_ref": str(source.source_key),
        "hypothesis_ref": str(card.card_key),
        "observed_at": card.card.hypothesis.ledger_recorded_at,
        "source_at": source.source.ledger_recorded_at,
        "hypothesis_at": card.card.hypothesis.ledger_recorded_at,
    }
    if base["code_ref"] is None:
        return ExperimentChain(**base, blocker="code_sha_invalid")
    if trial is None:
        return ExperimentChain(**base, blocker="dataset_sha_missing")
    base = base | {
        "dataset_sha": trial.registration.data_version,
        "trial_ref": str(trial.registration_key),
        "observed_at": trial.registration.registered_at,
        "trial_at": trial.registration.registered_at,
    }
    events = reader.trial_events(trial.registration.trial_id)
    terminal_at = trial.registration.registered_at
    terminal_ref: str | None = None
    match events:
        case ():
            return ExperimentChain(**base, blocker="trial_terminal_missing")
        case (*_, terminal) if terminal.event.event_kind.value == "started":
            return ExperimentChain(**base, blocker="trial_terminal_missing")
        case (*_, terminal):
            terminal_ref = str(terminal.event_key)
            terminal_at = terminal.event.occurred_at
            base = base | {
                "terminal_ref": terminal_ref,
                "observed_at": terminal.event.occurred_at,
                "trial_started_at": events[0].event.occurred_at,
                "terminal_at": terminal.event.occurred_at,
            }
    if not _monotonic(base):
        return ExperimentChain(**base, blocker="timestamp_order_invalid")
    if terminal_ref is None:
        return ExperimentChain(**base, blocker="trial_terminal_missing")
    matching_reviews = tuple(
        review
        for review in review_events
        if review.event.snapshot_key == terminal_ref
        and review.event.strategy_version == registration.strategy_version
        and review.event.experiment_scope_key == registration.experiment_scope_key
        and review.event.reviewer_action.value == "comparison_ready"
        and review.event.reviewed_at >= terminal_at
    )
    if len(matching_reviews) != 1:
        return ExperimentChain(**base, blocker="reviewer_missing")
    review = matching_reviews[0]
    reviewer_ref = str(review.event_key)
    base = base | {
        "reviewer_ref": reviewer_ref,
        "observed_at": review.event.reviewed_at,
        "reviewed_at": review.event.reviewed_at,
    }
    if not _monotonic(base):
        return ExperimentChain(**base, blocker="timestamp_order_invalid")
    lifecycles = tuple(
        lifecycle
        for lifecycle in reader.lifecycle_events(registration.strategy_version)
        if reviewer_ref in lifecycle.event.evidence_keys and lifecycle.event.decided_at >= review.event.reviewed_at
    )
    if not lifecycles:
        return ExperimentChain(**base, blocker="lifecycle_missing")
    lifecycle = lifecycles[-1]
    return ExperimentChain(
        **(
            base
            | {
                "lifecycle_ref": str(lifecycle.event_key),
                "observed_at": lifecycle.event.decided_at,
                "lifecycle_at": lifecycle.event.decided_at,
            }
        ),
        blocker=None,
    )


def _safe_ref(value: str) -> str | None:
    if len(value) == 40 and all(character in "0123456789abcdef" for character in value):
        return hashlib.sha256(value.encode()).hexdigest()
    return None


def _monotonic(values: dict[str, str | dt.datetime | None]) -> bool:
    timestamps = tuple(
        value
        for key in (
            "source_at",
            "hypothesis_at",
            "code_at",
            "trial_at",
            "trial_started_at",
            "terminal_at",
            "reviewed_at",
            "lifecycle_at",
        )
        if isinstance((value := values[key]), dt.datetime)
    )
    return timestamps == tuple(sorted(timestamps))
