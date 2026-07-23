from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import ResearchSourceKind, TrialEventKind, TrialKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.source_driven_hypothesis_queue_models import (
    HypothesisQueueRoute,
    InvalidSourceDrivenHypothesisQueueError,
    SourceDrivenHypothesisQueueArtifact,
    SourceDrivenHypothesisQueueItem,
    SourceDrivenHypothesisQueueSnapshot,
)

_AUTHORITATIVE_KINDS = frozenset(
    {
        ResearchSourceKind.ACADEMIC_PAPER,
        ResearchSourceKind.OFFICIAL_MARKET_RULE,
        ResearchSourceKind.OFFICIAL_PROVIDER_DOCUMENT,
        ResearchSourceKind.INTERNAL_OBSERVATION,
    }
)
_ROUTE_PRIORITY = {
    HypothesisQueueRoute.EVIDENCE_REVIEW: 0,
    HypothesisQueueRoute.STRATEGY_DESIGN: 1,
    HypothesisQueueRoute.HISTORICAL_REPLAY: 2,
    HypothesisQueueRoute.ACTIVE_RESEARCH: 3,
    HypothesisQueueRoute.INDEPENDENT_REVIEW: 4,
    HypothesisQueueRoute.RECOVERY: 5,
}


def project_source_driven_hypothesis_queue(
    reader: ExperimentLedgerReader,
) -> SourceDrivenHypothesisQueueArtifact:
    try:
        sources = {str(stored.source_key): stored.source for stored in reader.research_sources()}
        cards = reader.research_hypothesis_cards()
        versions = reader.strategy_versions()
        trials = reader.trials()
        if not cards:
            raise InvalidSourceDrivenHypothesisQueueError
        items: list[SourceDrivenHypothesisQueueItem] = []
        observed_times: list[dt.datetime] = []
        for stored_card in cards:
            card = stored_card.card
            card_sources = tuple(sources[key] for key in card.research_source_keys)
            matching_versions = tuple(
                stored.registration
                for stored in versions
                if stored.registration.hypothesis_id == card.hypothesis.hypothesis_id
            )
            version_names = tuple(sorted(item.strategy_version for item in matching_versions))
            latest_version = (
                max(
                    matching_versions,
                    key=lambda item: (item.ledger_recorded_at, item.strategy_version),
                )
                if matching_versions
                else None
            )
            matching_trials = tuple(
                stored.registration
                for stored in trials
                if stored.registration.trial_kind is TrialKind.HISTORICAL_REPLAY
                and latest_version is not None
                and stored.registration.strategy_version == latest_version.strategy_version
            )
            trial_ids = tuple(sorted(item.trial_id for item in matching_trials))
            latest_events = (
                reader.trial_events(max(matching_trials, key=lambda item: (item.registered_at, item.trial_id)).trial_id)
                if matching_trials
                else ()
            )
            source_kinds = tuple(sorted({source.source_kind for source in card_sources}, key=str))
            items.append(
                SourceDrivenHypothesisQueueItem(
                    card_key=str(stored_card.card_key),
                    hypothesis_id=card.hypothesis.hypothesis_id,
                    lane_id=card.hypothesis.primary_lane,
                    registered_at=card.hypothesis.ledger_recorded_at,
                    hypothesis=card.hypothesis.hypothesis,
                    falsification_rule=card.hypothesis.falsification_rule,
                    economic_mechanism=card.economic_mechanism,
                    counterfactual_baseline=card.counterfactual_baseline,
                    source_keys=card.research_source_keys,
                    source_kinds=source_kinds,
                    strategy_versions=version_names,
                    historical_trial_ids=trial_ids,
                    route=_route(
                        source_kinds,
                        version_names,
                        trial_ids,
                        latest_events[-1].event.event_kind if latest_events else None,
                    ),
                )
            )
            observed_times.extend(source.ledger_recorded_at for source in card_sources)
            observed_times.append(card.hypothesis.ledger_recorded_at)
            observed_times.extend(item.ledger_recorded_at for item in matching_versions)
            observed_times.extend(item.registered_at for item in matching_trials)
            observed_times.extend(item.event.occurred_at for item in latest_events)
        snapshot = SourceDrivenHypothesisQueueSnapshot(
            as_of=max(observed_times),
            items=tuple(sorted(items, key=_item_order)),
        )
        snapshot_id = hashlib.sha256(canonical_experiment_ledger_json(snapshot).encode()).hexdigest()
        return SourceDrivenHypothesisQueueArtifact(snapshot_id=snapshot_id, snapshot=snapshot)
    except (
        InvalidExperimentLedgerSourceError,
        InvalidSourceDrivenHypothesisQueueError,
        KeyError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidSourceDrivenHypothesisQueueError from None


def publish_source_driven_hypothesis_queue(
    root: Path,
    artifact: SourceDrivenHypothesisQueueArtifact,
) -> tuple[Path, bool]:
    try:
        checked = SourceDrivenHypothesisQueueArtifact.model_validate(artifact.model_dump(mode="python"))
        path = root / f"source_hypothesis_queue_{checked.snapshot_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidSourceDrivenHypothesisQueueError from None


def load_source_driven_hypothesis_queue(path: Path) -> SourceDrivenHypothesisQueueArtifact:
    try:
        payload = read_private_text(path)
        artifact = SourceDrivenHypothesisQueueArtifact.model_validate_json(payload)
        if path.name != f"source_hypothesis_queue_{artifact.snapshot_id}.json" or payload != _payload(artifact):
            raise InvalidSourceDrivenHypothesisQueueError
        return artifact
    except InvalidSourceDrivenHypothesisQueueError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidSourceDrivenHypothesisQueueError from None


def _payload(artifact: SourceDrivenHypothesisQueueArtifact) -> str:
    return canonical_experiment_ledger_json(artifact) + "\n"


def _route(
    source_kinds: tuple[ResearchSourceKind, ...],
    strategy_versions: tuple[str, ...],
    trial_ids: tuple[str, ...],
    latest_event_kind: TrialEventKind | None,
) -> HypothesisQueueRoute:
    if not any(kind in _AUTHORITATIVE_KINDS for kind in source_kinds):
        return HypothesisQueueRoute.EVIDENCE_REVIEW
    if not strategy_versions:
        return HypothesisQueueRoute.STRATEGY_DESIGN
    if not trial_ids:
        return HypothesisQueueRoute.HISTORICAL_REPLAY
    match latest_event_kind:
        case None | TrialEventKind.STARTED:
            return HypothesisQueueRoute.ACTIVE_RESEARCH
        case TrialEventKind.COMPLETED:
            return HypothesisQueueRoute.INDEPENDENT_REVIEW
        case TrialEventKind.FAILED | TrialEventKind.CENSORED:
            return HypothesisQueueRoute.RECOVERY
        case unreachable:
            assert_never(unreachable)


def _item_order(item: SourceDrivenHypothesisQueueItem) -> tuple[int, dt.datetime, str]:
    return (_ROUTE_PRIORITY[item.route], item.registered_at, item.hypothesis_id)


__all__ = (
    "HypothesisQueueRoute",
    "InvalidSourceDrivenHypothesisQueueError",
    "SourceDrivenHypothesisQueueArtifact",
    "SourceDrivenHypothesisQueueItem",
    "SourceDrivenHypothesisQueueSnapshot",
    "load_source_driven_hypothesis_queue",
    "project_source_driven_hypothesis_queue",
    "publish_source_driven_hypothesis_queue",
)
