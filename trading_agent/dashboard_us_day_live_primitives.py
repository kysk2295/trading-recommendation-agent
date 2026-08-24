from __future__ import annotations

import datetime as dt
from typing import Literal

from trading_agent.dashboard_models_v2 import TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text


def day_live_item(
    item_id: str,
    kind: Literal["day_theme", "day_recommendation", "day_agent_version", "paper"],
    label: str,
    value: str,
    observed_at: dt.datetime,
    source: str,
) -> WorkspaceItemV2:
    return WorkspaceItemV2(
        item_id=item_id,
        kind=kind,
        label=redact_outbound_text(label, max_chars=80),
        state="populated",
        value=redact_outbound_text(value, max_chars=160),
        observed_at=observed_at,
        trace_id=source,
    )


def day_live_node(
    node_id: str,
    kind: Literal["source_receipt", "reviewer_decision", "paper_receipt", "blocker_terminal"],
    label: str,
    observed_at: dt.datetime,
    safe_ref: str,
    state: Literal["accepted", "blocked", "unavailable"],
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=label,
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace="day.live",
    )


__all__ = ("day_live_item", "day_live_node")
