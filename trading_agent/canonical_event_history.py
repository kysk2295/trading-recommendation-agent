from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import override

from trading_agent.canonical_dataset_event_reader import replay_canonical_dataset_events
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplayError
from trading_agent.canonical_event_models import (
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)

_ERROR_MESSAGE = "canonical event history could not be replayed"


class CanonicalEventHistoryError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "CanonicalEventHistoryError()"


@dataclass(frozen=True, slots=True)
class CanonicalEventHistoryReplay:
    as_of: dt.datetime
    dataset_ids: tuple[str, ...]
    observed_event_count: int
    active_events: tuple[CanonicalEventEnvelope, ...] = field(repr=False)
    superseded_event_ids: tuple[str, ...]
    tombstoned_root_event_ids: tuple[str, ...]


def replay_canonical_event_history(
    dataset_directories: Sequence[Path],
    *,
    as_of: dt.datetime,
) -> CanonicalEventHistoryReplay:
    try:
        paths = tuple(dataset_directories)
        if not paths or not _aware(as_of) or any(not isinstance(path, Path) for path in paths):
            raise ValueError
        dataset_ids, events = _read_datasets(paths)
        ordered = _ordered_unique_events(events)
        _validate_complete_chain(ordered)
        return _materialize(ordered, dataset_ids=dataset_ids, as_of=as_of)
    except (CanonicalDatasetReplayError, TypeError, ValueError):
        raise CanonicalEventHistoryError from None


def _read_datasets(
    paths: tuple[Path, ...],
) -> tuple[tuple[str, ...], tuple[CanonicalEventEnvelope, ...]]:
    dataset_ids: set[str] = set()
    events: list[CanonicalEventEnvelope] = []
    for path in paths:
        replay, verified_events = replay_canonical_dataset_events(path)
        dataset_ids.add(replay.dataset_id)
        events.extend(verified_events)
    return tuple(sorted(dataset_ids)), tuple(events)


def _ordered_unique_events(
    events: tuple[CanonicalEventEnvelope, ...],
) -> tuple[CanonicalEventEnvelope, ...]:
    by_id: dict[str, CanonicalEventEnvelope] = {}
    for event in events:
        existing = by_id.get(event.event_id)
        if existing is not None and existing != event:
            raise ValueError
        by_id[event.event_id] = event
    return tuple(
        sorted(
            by_id.values(),
            key=lambda event: (event.normalized_at, event.received_at, event.event_id),
        )
    )


def _validate_complete_chain(events: tuple[CanonicalEventEnvelope, ...]) -> None:
    known: dict[str, CanonicalEventEnvelope] = {}
    active: set[str] = set()
    for event in events:
        if event.operation is CanonicalEventOperation.ORIGINAL:
            known[event.event_id] = event
            active.add(event.event_id)
            continue
        target_id = event.correction_of
        target = known.get(target_id or "")
        if target is None or target.event_id not in active or not _same_identity(target, event):
            raise ValueError
        if target.received_at > event.received_at or target.normalized_at > event.normalized_at:
            raise ValueError
        active.remove(target.event_id)
        known[event.event_id] = event
        if event.operation is CanonicalEventOperation.CORRECTION:
            active.add(event.event_id)


def _same_identity(
    target: CanonicalEventEnvelope,
    successor: CanonicalEventEnvelope,
) -> bool:
    return (
        target.source_id == successor.source_id
        and target.event_type == successor.event_type
        and target.provider_event_id == successor.provider_event_id
        and target.entity_refs == successor.entity_refs
    )


def _materialize(
    events: tuple[CanonicalEventEnvelope, ...],
    *,
    dataset_ids: tuple[str, ...],
    as_of: dt.datetime,
) -> CanonicalEventHistoryReplay:
    visible = tuple(event for event in events if event.normalized_at <= as_of)
    active: dict[str, CanonicalEventEnvelope] = {}
    roots: dict[str, str] = {}
    superseded: set[str] = set()
    tombstoned_roots: set[str] = set()
    for event in visible:
        if event.operation is CanonicalEventOperation.ORIGINAL:
            active[event.event_id] = event
            roots[event.event_id] = event.event_id
            continue
        target_id = event.correction_of
        if target_id is None or target_id not in active:
            raise ValueError
        root_id = roots[target_id]
        del active[target_id]
        superseded.add(target_id)
        roots[event.event_id] = root_id
        if event.operation is CanonicalEventOperation.CORRECTION:
            active[event.event_id] = event
        else:
            tombstoned_roots.add(root_id)
    return CanonicalEventHistoryReplay(
        as_of=as_of,
        dataset_ids=dataset_ids,
        observed_event_count=len(visible),
        active_events=tuple(sorted(active.values(), key=lambda event: event.event_id)),
        superseded_event_ids=tuple(sorted(superseded)),
        tombstoned_root_event_ids=tuple(sorted(tombstoned_roots)),
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "CanonicalEventHistoryError",
    "CanonicalEventHistoryReplay",
    "replay_canonical_event_history",
)
