from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateName,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_session_terminal_blocked import (
    blocked_session_terminal_projection,
)
from trading_agent.dashboard_session_terminal_source import (
    InvalidDashboardSessionTerminalSourceError,
    read_private_session_terminal_events,
)
from trading_agent.hermes_delivery_models import HermesDeliveryEvent, HermesDeliveryKind

_NEW_YORK: Final = ZoneInfo("America/New_York")
_TERMINAL_PREFIXES: Final = (
    "us-session-terminal-",
    "us-day-no-recommendation-",
    "us-day-missing-terminal-",
    "kr-terminal:",
    "kr-exit:",
    "kr-source-preflight-incident-",
)


class SessionTerminalOutcome(StrEnum):
    RECOMMENDATION = "recommendation"
    NO_RECOMMENDATION = "no_recommendation"
    INCIDENT = "incident"


class _SessionTerminalMarket(StrEnum):
    US = "us_equities"
    KR = "kr_equities"


class _SessionTerminalKind(StrEnum):
    RECOMMENDATION_SUMMARY = HermesDeliveryKind.DAILY_SUMMARY.value
    RECOMMENDATION_EXIT = HermesDeliveryKind.EXIT.value
    NO_RECOMMENDATION = HermesDeliveryKind.NO_RECOMMENDATION.value
    INCIDENT = HermesDeliveryKind.INCIDENT.value


class InvalidDashboardSessionTerminalError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Terminal:
    market_id: str
    session_date: dt.date
    observed_at: dt.datetime
    outcome: SessionTerminalOutcome
    safe_ref: str


def project_session_terminals(
    base: WorkspaceProjection,
    outputs: Path,
    *,
    now: dt.datetime,
) -> WorkspaceProjection:
    database = outputs / "hermes" / "delivery.sqlite3"
    if not database.exists() and not database.is_symlink():
        return base
    try:
        events = read_private_session_terminal_events(database)
        terminals = _terminal_events(events, now)
    except (InvalidDashboardSessionTerminalError, InvalidDashboardSessionTerminalSourceError):
        return blocked_session_terminal_projection(base, now)
    if not terminals:
        return base
    items = tuple(_item(terminal) for terminal in terminals)
    nodes = tuple(node for terminal in terminals for node in _nodes(terminal))
    edges = tuple(_edge(terminal) for terminal in terminals)
    incident = next((terminal for terminal in terminals if terminal.outcome is SessionTerminalOutcome.INCIDENT), None)
    base_state = base.workspace.state
    base_blocked = base_state in {"error", "blocked", "unavailable", "corrupt"}
    state: SourceStateName = base_state if base_blocked or incident is None else "blocked"
    blocker = base.workspace.blocker_code if base_blocked or incident is None else "session_terminal_incident"
    root_edges = (
        ()
        if base_blocked or incident is None
        else (
            TraceEdgeV2(
                from_node_id=base.workspace.trace_id, to_node_id=_terminal_node_id(incident), kind="blocked_by"
            ),
        )
    )
    observed_at = (
        max(terminal.observed_at for terminal in terminals)
        if base.workspace.observed_at is None
        else max(
            base.workspace.observed_at,
            *(terminal.observed_at for terminal in terminals),
        )
    )
    workspace = SourceStateV2(
        **base.workspace.model_dump(
            exclude={
                "state",
                "observed_at",
                "freshness",
                "blocker_code",
                "summary",
                "total_count",
                "projected_count",
                "truncated",
                "items",
            }
        ),
        state=state,
        observed_at=observed_at,
        freshness=FreshnessV2(
            policy_id="authoritative-market-session-terminal-v1",
            age_seconds=max(0, int((now - observed_at).total_seconds())),
            as_of=now,
        ),
        blocker_code=blocker,
        summary="Authoritative calendars, KR realtime cycle, and Hermes session terminals projected",
        total_count=base.workspace.total_count + len(items),
        projected_count=base.workspace.projected_count + len(items),
        truncated=False,
        items=(*base.workspace.items, *items),
    )
    return WorkspaceProjection(workspace, (*base.nodes, *nodes), (*base.edges, *edges, *root_edges))


def _terminal_events(
    events: tuple[HermesDeliveryEvent, ...],
    now: dt.datetime,
) -> tuple[_Terminal, ...]:
    grouped: defaultdict[tuple[str, dt.date], list[_Terminal]] = defaultdict(list)
    for event in events:
        if not event.source_event_id.startswith(_TERMINAL_PREFIXES):
            continue
        terminal = _parse_terminal(event, now)
        grouped[(terminal.market_id, terminal.session_date)].append(terminal)
    terminals = tuple(
        _terminal_group(market_id, session_date, tuple(group)) for (market_id, session_date), group in grouped.items()
    )
    return tuple(sorted(terminals, key=lambda item: (item.observed_at, item.market_id), reverse=True)[:20])


def _parse_terminal(event: HermesDeliveryEvent, now: dt.datetime) -> _Terminal:
    if event.occurred_at > now + dt.timedelta(minutes=5):
        raise InvalidDashboardSessionTerminalError("future_session_terminal")
    try:
        market = _SessionTerminalMarket(event.market_id)
    except ValueError:
        raise InvalidDashboardSessionTerminalError("invalid_session_terminal_market") from None
    match market:
        case _SessionTerminalMarket.US:
            session_date = event.occurred_at.astimezone(_NEW_YORK).date()
        case _SessionTerminalMarket.KR:
            session_date = event.occurred_at.astimezone(ZoneInfo("Asia/Seoul")).date()
        case unreachable:
            assert_never(unreachable)
    return _Terminal(
        market_id=event.market_id,
        session_date=session_date,
        observed_at=event.occurred_at,
        outcome=_outcome(event.kind),
        safe_ref=event.payload_sha256,
    )


def _terminal_group(
    market_id: str,
    session_date: dt.date,
    terminals: tuple[_Terminal, ...],
) -> _Terminal:
    if len(terminals) == 1:
        return terminals[0]
    observed_at = max(item.observed_at for item in terminals)
    safe_ref = hashlib.sha256(":".join(sorted(item.safe_ref for item in terminals)).encode()).hexdigest()
    outcomes = frozenset(item.outcome for item in terminals)
    outcome = (
        SessionTerminalOutcome.RECOMMENDATION
        if outcomes == {SessionTerminalOutcome.RECOMMENDATION}
        else SessionTerminalOutcome.INCIDENT
    )
    return _Terminal(market_id, session_date, observed_at, outcome, safe_ref)


def _outcome(kind: HermesDeliveryKind) -> SessionTerminalOutcome:
    try:
        terminal_kind = _SessionTerminalKind(kind.value)
    except ValueError:
        raise InvalidDashboardSessionTerminalError("invalid_session_terminal_kind") from None
    match terminal_kind:
        case _SessionTerminalKind.RECOMMENDATION_SUMMARY | _SessionTerminalKind.RECOMMENDATION_EXIT:
            return SessionTerminalOutcome.RECOMMENDATION
        case _SessionTerminalKind.NO_RECOMMENDATION:
            return SessionTerminalOutcome.NO_RECOMMENDATION
        case _SessionTerminalKind.INCIDENT:
            return SessionTerminalOutcome.INCIDENT
        case unreachable:
            assert_never(unreachable)


def _item(terminal: _Terminal) -> WorkspaceItemV2:
    state: SourceStateName = "blocked" if terminal.outcome is SessionTerminalOutcome.INCIDENT else "populated"
    source_id = _source_node_id(terminal)
    market = "US" if terminal.market_id == "us_equities" else "KR"
    return WorkspaceItemV2(
        item_id=f"session_terminal.{terminal.market_id}.{terminal.session_date:%Y%m%d}",
        kind="metric",
        label=f"{market} {terminal.session_date.isoformat()} session terminal",
        state=state,
        value=terminal.outcome.value,
        observed_at=terminal.observed_at,
        trace_id=source_id,
    )


def _nodes(terminal: _Terminal) -> tuple[TraceNodeV2, TraceNodeV2]:
    source = _source_node_id(terminal)
    terminal_id = _terminal_node_id(terminal)
    blocked = terminal.outcome is SessionTerminalOutcome.INCIDENT
    return (
        TraceNodeV2(
            node_id=source,
            kind="source_receipt",
            label="Hermes session terminal source",
            observed_at=terminal.observed_at,
            safe_ref=terminal.safe_ref,
            state="accepted",
            source_namespace="dashboard.session_terminal",
        ),
        TraceNodeV2(
            node_id=terminal_id,
            kind="blocker_terminal" if blocked else "reviewer_decision",
            label=terminal.outcome.value,
            observed_at=terminal.observed_at,
            safe_ref=terminal.safe_ref,
            state="blocked" if blocked else "accepted",
            source_namespace="dashboard.session_terminal",
        ),
    )


def _edge(terminal: _Terminal) -> TraceEdgeV2:
    return TraceEdgeV2(
        from_node_id=_source_node_id(terminal),
        to_node_id=_terminal_node_id(terminal),
        kind="blocked_by" if terminal.outcome is SessionTerminalOutcome.INCIDENT else "reviewed_by",
    )


def _source_node_id(terminal: _Terminal) -> str:
    return f"trace.markets.session.{terminal.market_id}.{terminal.session_date:%Y%m%d}"


def _terminal_node_id(terminal: _Terminal) -> str:
    return f"{_source_node_id(terminal)}.terminal"


__all__ = ("project_session_terminals",)
