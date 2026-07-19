from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import assert_never

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    lifecycle_state_rank,
)
from trading_agent.multi_market_experiment_keys import (
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_experiment_models import MultiMarketStrategyVersionRegistration
from trading_agent.multi_market_experiment_store import (
    read_multi_market_hypotheses,
    read_multi_market_strategy_versions,
)
from trading_agent.multi_market_lifecycle_keys import (
    MultiMarketLifecycleEventKey,
    multi_market_lifecycle_event_key,
)
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent
from trading_agent.research_identity_models import AgentOperatingMode


class InvalidMultiMarketLifecycleSourceError(ValueError):
    def __str__(self) -> str:
        return "invalid multi-market lifecycle source"


class MultiMarketLifecycleConflictError(RuntimeError):
    def __str__(self) -> str:
        return "multi-market lifecycle immutable identity conflicts"


@dataclass(frozen=True, slots=True)
class StoredMultiMarketLifecycleEvent:
    event_key: MultiMarketLifecycleEventKey
    event: MultiMarketStrategyLifecycleEvent


@dataclass(frozen=True, slots=True)
class _LifecycleParent:
    version: MultiMarketStrategyVersionRegistration
    hypothesis_key: str


def read_multi_market_lifecycle_events(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> tuple[StoredMultiMarketLifecycleEvent, ...]:
    rows: list[tuple[str, str, str, str, str, int, str, str, str | None, str]] = connection.execute(
        """SELECT event_key,strategy_version,strategy_lane_id,market_id,agent_family,
        sequence,event_kind,effective_session_date,previous_event_key,payload_json
        FROM multi_market_lifecycle_events WHERE strategy_version=? ORDER BY sequence""",
        (strategy_version,),
    ).fetchall()
    events = tuple(_stored_event(row) for row in rows)
    parent = _version_parent(connection, strategy_version)
    _require_chain(parent, events)
    return events


def append_multi_market_lifecycle_event(
    connection: sqlite3.Connection,
    event: MultiMarketStrategyLifecycleEvent,
) -> bool:
    event = _validated_event(event)
    parent = _version_parent(connection, event.strategy_version)
    if parent is None:
        raise InvalidMultiMarketLifecycleSourceError
    events = read_multi_market_lifecycle_events(connection, event.strategy_version)
    key = multi_market_lifecycle_event_key(event)
    existing = next((stored for stored in events if stored.event.sequence == event.sequence), None)
    if existing is not None:
        if existing.event_key == key and existing.event == event:
            return False
        raise MultiMarketLifecycleConflictError
    candidate = (*events, StoredMultiMarketLifecycleEvent(key, event))
    _require_chain(parent, candidate)
    try:
        _ = connection.execute(
            "INSERT INTO multi_market_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                event.strategy_version,
                event.strategy_lane.canonical_id,
                event.strategy_lane.market_id.value,
                event.strategy_lane.agent_family.value,
                event.sequence,
                event.event_kind.value,
                event.effective_session_date.isoformat(),
                event.previous_event_key,
                canonical_experiment_ledger_json(event),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise MultiMarketLifecycleConflictError from error
    return True


def _version_parent(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> _LifecycleParent | None:
    versions = tuple(
        stored
        for stored in read_multi_market_strategy_versions(connection)
        if stored.registration.strategy_version == strategy_version
    )
    if not versions:
        return None
    if len(versions) != 1:
        raise InvalidMultiMarketLifecycleSourceError
    version = versions[0]
    hypotheses = tuple(
        stored
        for stored in read_multi_market_hypotheses(connection)
        if stored.registration.hypothesis_id == version.registration.hypothesis_id
    )
    if (
        len(hypotheses) != 1
        or hypotheses[0].registration.experiment_scope_key != version.registration.experiment_scope_key
        or hypotheses[0].registration.experiment_scope.primary_lane != version.registration.strategy_lane
    ):
        raise InvalidMultiMarketLifecycleSourceError
    return _LifecycleParent(version.registration, str(hypotheses[0].registration_key))


def _require_chain(
    parent: _LifecycleParent | None,
    events: tuple[StoredMultiMarketLifecycleEvent, ...],
) -> None:
    if parent is None:
        if events:
            raise InvalidMultiMarketLifecycleSourceError
        return
    previous: StoredMultiMarketLifecycleEvent | None = None
    for expected_sequence, stored in enumerate(events, start=1):
        event = stored.event
        if (
            event.strategy_version != parent.version.strategy_version
            or event.strategy_lane != parent.version.strategy_lane
            or event.sequence != expected_sequence
            or event.decided_at < parent.version.ledger_recorded_at
            or not _mode_supports(parent.version.operating_mode, event.to_state)
        ):
            raise InvalidMultiMarketLifecycleSourceError
        if previous is None:
            _require_registration_evidence(parent, event)
        else:
            _require_transition(previous, events[: expected_sequence - 1], event)
        previous = stored


def _require_registration_evidence(
    parent: _LifecycleParent,
    event: MultiMarketStrategyLifecycleEvent,
) -> None:
    expected = tuple(
        sorted(
            (
                parent.hypothesis_key,
                parent.version.experiment_scope_key,
                event.session_calendar_snapshot_id,
                str(multi_market_strategy_version_registration_key(parent.version)),
            )
        )
    )
    if event.event_kind is not StrategyLifecycleEventKind.REGISTRATION or event.evidence_keys != expected:
        raise InvalidMultiMarketLifecycleSourceError


def _require_transition(
    previous: StoredMultiMarketLifecycleEvent,
    history: tuple[StoredMultiMarketLifecycleEvent, ...],
    event: MultiMarketStrategyLifecycleEvent,
) -> None:
    if (
        previous.event.to_state is StrategyLifecycleState.REJECTED
        or event.event_kind is not StrategyLifecycleEventKind.TRANSITION
        or event.previous_event_key != previous.event_key
        or event.from_state is not previous.event.to_state
        or event.decided_at < previous.event.decided_at
        or previous.event.effective_session_date > event.decision_session_date
        or event.previous_event_key not in event.evidence_keys
        or event.session_calendar_snapshot_id not in event.evidence_keys
    ):
        raise InvalidMultiMarketLifecycleSourceError
    if previous.event.to_state is StrategyLifecycleState.SUSPENDED:
        active = next(
            (
                stored.event.to_state
                for stored in reversed(history[:-1])
                if stored.event.to_state is not StrategyLifecycleState.SUSPENDED
            ),
            None,
        )
        if active is None or lifecycle_state_rank(event.to_state) > lifecycle_state_rank(active):
            raise InvalidMultiMarketLifecycleSourceError


def _mode_supports(mode: AgentOperatingMode, state: StrategyLifecycleState) -> bool:
    match mode:
        case AgentOperatingMode.CONTRACT_ONLY:
            return state in {StrategyLifecycleState.IDEA, StrategyLifecycleState.HISTORICAL}
        case AgentOperatingMode.SHADOW:
            return state not in {
                StrategyLifecycleState.EXPERIMENTAL_PAPER,
                StrategyLifecycleState.PAPER_CHAMPION,
            }
        case AgentOperatingMode.ALPACA_PAPER:
            return True
        case unreachable:
            assert_never(unreachable)


def _stored_event(
    row: tuple[str, str, str, str, str, int, str, str, str | None, str],
) -> StoredMultiMarketLifecycleEvent:
    key, version, lane, market, family, sequence, kind, effective, previous, payload = row
    try:
        event = MultiMarketStrategyLifecycleEvent.model_validate_json(payload)
    except (TypeError, ValidationError, ValueError):
        raise InvalidMultiMarketLifecycleSourceError from None
    typed_key = MultiMarketLifecycleEventKey(key)
    if (
        typed_key != multi_market_lifecycle_event_key(event)
        or version != event.strategy_version
        or lane != event.strategy_lane.canonical_id
        or market != event.strategy_lane.market_id.value
        or family != event.strategy_lane.agent_family.value
        or sequence != event.sequence
        or kind != event.event_kind.value
        or effective != event.effective_session_date.isoformat()
        or previous != event.previous_event_key
    ):
        raise InvalidMultiMarketLifecycleSourceError
    return StoredMultiMarketLifecycleEvent(typed_key, event)


def _validated_event(event: MultiMarketStrategyLifecycleEvent) -> MultiMarketStrategyLifecycleEvent:
    try:
        return MultiMarketStrategyLifecycleEvent.model_validate(event.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidMultiMarketLifecycleSourceError from None
