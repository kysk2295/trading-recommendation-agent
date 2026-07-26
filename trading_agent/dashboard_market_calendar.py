from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)

US_SESSION_FILE = "us-session-receipts.v2.jsonl"
NEW_YORK = ZoneInfo("America/New_York")
SEOUL = ZoneInfo("Asia/Seoul")
Workspace = Literal["overview", "markets"]


class UsSessionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["us_market_calendar"]
    epoch_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    session_date: dt.date
    observed_at: dt.datetime
    session_state: Literal["open", "closed"]
    calendar_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_receipt(self) -> UsSessionReceipt:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError
        return self


def project_market_calendar(
    outputs: Path,
    *,
    now: dt.datetime,
    workspace: Workspace,
) -> WorkspaceProjection:
    kr = _kr_calendar(outputs, now, workspace)
    us = _us_calendar(outputs, now, workspace)
    parts = (kr, us)
    states = tuple(part[0].state for part in parts)
    missing = "unavailable" in states or "corrupt" in states
    stale = "stale" in states
    root_id = f"trace.{workspace}.calendar"
    blocker_id = f"{root_id}.blocker"
    safe_ref = hashlib.sha256(f"{workspace}:calendar".encode()).hexdigest()
    blocked = missing
    nodes = tuple(node for _, part_nodes, _ in parts for node in part_nodes)
    edges = tuple(edge for _, _, part_edges in parts for edge in part_edges)
    root_nodes = (
        _node(root_id, "source_receipt", now, safe_ref, "accepted", workspace),
        *(() if not blocked else (_node(blocker_id, "blocker_terminal", now, safe_ref, "blocked", workspace),)),
    )
    root_edges = (
        () if not blocked else (TraceEdgeV2(from_node_id=root_id, to_node_id=blocker_id, kind="blocked_by"),)
    )
    state = "blocked" if blocked else "stale" if stale else "populated"
    return WorkspaceProjection(
        SourceStateV2(
            state=state,
            observed_at=max(
                (item.observed_at for item, _, _ in parts if item.observed_at is not None),
                default=now,
            ),
            freshness=FreshnessV2(policy_id="authoritative-market-calendar-v2", age_seconds=0, as_of=now),
            blocker_code="market_calendar_missing" if blocked else None,
            summary="Authoritative KR and US market calendars projected",
            total_count=2,
            projected_count=2,
            truncated=False,
            trace_id=root_id,
            items=tuple(item for item, _, _ in parts),
        ),
        nodes + root_nodes,
        edges + root_edges,
    )


def _kr_calendar(
    outputs: Path,
    now: dt.datetime,
    workspace: Workspace,
) -> tuple[WorkspaceItemV2, tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    path = outputs / "live_sessions" / "kis_kr_session_calendar.sqlite3"
    try:
        snapshots = KisKrSessionCalendarStore(path).snapshots()
    except (InvalidKisKrSessionCalendarStoreError, OSError):
        return _missing("kr", now, workspace, "market_calendar_invalid")
    if not snapshots:
        return _missing("kr", now, workspace, "market_calendar_missing")
    snapshot = max(snapshots, key=lambda item: item.payload.observed_at)
    target = now.astimezone(SEOUL).date()
    day = next((item for item in snapshot.payload.days if item.session_date == target), None)
    if day is None:
        return _missing("kr", now, workspace, "market_calendar_date_missing")
    observed_at = snapshot.payload.observed_at
    if observed_at > now + dt.timedelta(minutes=5):
        return _missing("kr", now, workspace, "market_calendar_future")
    stale = now - observed_at > dt.timedelta(hours=36)
    return _accepted(
        "kr",
        "scheduled" if day.open_day else "closed",
        observed_at,
        snapshot.snapshot_id,
        stale,
        workspace,
    )


def _us_calendar(
    outputs: Path,
    now: dt.datetime,
    workspace: Workspace,
) -> tuple[WorkspaceItemV2, tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    path = outputs / "live_sessions" / US_SESSION_FILE
    if not path.exists():
        return _missing("us", now, workspace, "market_calendar_missing")
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            return _missing("us", now, workspace, "market_calendar_invalid")
        payload = path.read_bytes()
        receipts = tuple(UsSessionReceipt.model_validate_json(line) for line in payload.splitlines())
    except (OSError, ValidationError, ValueError):
        return _missing("us", now, workspace, "market_calendar_invalid")
    if not receipts or len({item.epoch_id for item in receipts}) != 1:
        return _missing("us", now, workspace, "market_calendar_mixed_epoch")
    eligible = tuple(item for item in receipts if item.observed_at <= now + dt.timedelta(minutes=5))
    if len(eligible) != len(receipts):
        return _missing("us", now, workspace, "market_calendar_future")
    latest = max(eligible, key=lambda item: item.observed_at)
    stale = (
        latest.session_date != now.astimezone(NEW_YORK).date()
        or now - latest.observed_at > dt.timedelta(hours=36)
    )
    return _accepted(
        "us",
        latest.session_state,
        latest.observed_at,
        latest.calendar_sha256,
        stale,
        workspace,
    )


def _accepted(
    market: str,
    value: str,
    observed_at: dt.datetime,
    safe_ref: str,
    stale: bool,
    workspace: Workspace,
) -> tuple[WorkspaceItemV2, tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    source_id = f"trace.{workspace}.calendar.{market}"
    return (
        WorkspaceItemV2(
            item_id=f"market.{market}.session",
            kind="metric",
            label=f"{market.upper()} session",
            state="stale" if stale else "populated",
            value=value,
            observed_at=observed_at,
            trace_id=source_id,
        ),
        (_node(source_id, "source_receipt", observed_at, safe_ref, "accepted", workspace),),
        (),
    )


def _missing(
    market: str,
    now: dt.datetime,
    workspace: Workspace,
    reason: str,
) -> tuple[WorkspaceItemV2, tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    source_id = f"trace.{workspace}.calendar.{market}"
    blocker_id = f"{source_id}.blocker"
    safe_ref = hashlib.sha256(f"{market}:{reason}".encode()).hexdigest()
    return (
        WorkspaceItemV2(
            item_id=f"market.{market}.session",
            kind="metric",
            label=f"{market.upper()} session",
            state="unavailable",
            value=None,
            observed_at=None,
            trace_id=source_id,
        ),
        (
            _node(source_id, "source_receipt", now, safe_ref, "unavailable", workspace),
            _node(blocker_id, "blocker_terminal", now, safe_ref, "blocked", workspace),
        ),
        (TraceEdgeV2(from_node_id=source_id, to_node_id=blocker_id, kind="blocked_by"),),
    )


def _node(
    node_id: str,
    kind: Literal["source_receipt", "blocker_terminal"],
    observed_at: dt.datetime,
    safe_ref: str,
    state: Literal["accepted", "blocked", "unavailable"],
    workspace: Workspace,
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label="Authoritative market calendar",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace=f"market_calendar.{workspace}",
    )
