from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_us_day_live_primitives import day_live_item, day_live_node
from trading_agent.dashboard_us_day_live_thesis_render import render_us_day_paper_items, render_us_day_thesis_items
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle
from trading_agent.dashboard_us_day_versions import DayAgentVersionReader, DayAgentVersionView, read_day_versions
from trading_agent.day_agent_task_models import DayAgentResearchTask
from trading_agent.day_agent_task_store import DayAgentTaskReader
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.models import RecommendationEvent
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayThesisChange, UsDayTradeThesis


@dataclass(frozen=True, slots=True)
class DayLiveReaders:
    theses: tuple[UsDayTradeThesis, ...]
    changes: Mapping[str, tuple[UsDayThesisChange, ...]]
    paper_events: Mapping[str, tuple[RecommendationEvent, ...]]
    task_reader: DayAgentTaskReader | None
    version_reader: DayAgentVersionReader | None


@dataclass(frozen=True, slots=True)
class DayLiveProjection:
    markets: tuple[WorkspaceItemV2, ...]
    paper: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def render_us_day_live(
    readers: DayLiveReaders,
    reports: tuple[MarketCloseReport, ...],
    now: dt.datetime,
    paper_ledger: FinalizedPaperProjectionBundle | None,
) -> DayLiveProjection:
    source = "trace.day.source"
    ordered = tuple(sorted(readers.theses, key=lambda item: (item.observed_at, item.thesis_id), reverse=True))
    version_read = read_day_versions(readers.version_reader, readers.task_reader, now=now)
    market_items = _version_market_items(readers, version_read.records, version_read.blocker_code, source, now)
    actionable = tuple(item for item in ordered if item.decision is DayTradeDecision.RECOMMEND)
    market_items.extend(_lead_market_items(actionable, source))
    paper_items: list[WorkspaceItemV2] = []
    terminal_index = 0
    for thesis in actionable + tuple(item for item in ordered if item.decision is not DayTradeDecision.RECOMMEND):
        if thesis.decision is not DayTradeDecision.RECOMMEND:
            terminal_index += 1
        market_items.extend(
            render_us_day_thesis_items(
                thesis,
                readers.changes[thesis.thesis_id],
                readers.paper_events.get(thesis.thesis_id, ()),
                terminal_index,
                source,
            )
        )
        if thesis.decision is DayTradeDecision.RECOMMEND and paper_ledger is not None:
            paper_items.extend(render_us_day_paper_items(thesis, paper_ledger, source))
    paper_items.extend(_close_report_items(reports, source))
    safe_ref = hashlib.sha256(":".join(item.thesis_id for item in ordered).encode()).hexdigest()
    observed_at = max((item.observed_at for item in (*ordered, *version_read.records)), default=now)
    nodes, edges = _trace_graph(source, observed_at, safe_ref, version_read.blocker_code, now)
    return DayLiveProjection(tuple(market_items), tuple(paper_items), nodes, edges)


def _version_market_items(
    readers: DayLiveReaders,
    versions: tuple[DayAgentVersionView, ...],
    blocker_code: str | None,
    source: str,
    now: dt.datetime,
) -> list[WorkspaceItemV2]:
    market_items: list[WorkspaceItemV2] = []
    if blocker_code is not None:
        market_items.append(
            WorkspaceItemV2(
                item_id="day.version_source",
                kind="system",
                label="Day version source",
                state="blocked",
                value=blocker_code,
                observed_at=now,
                trace_id=source,
            )
        )
    champion = next((item for item in versions if item.deployment_state == "champion"), None)
    if champion is not None:
        market_items.append(_version_item("day.champion", "Current Champion", champion, source))
        task = _task(readers.task_reader, champion.task_id)
        if task is not None and task.current_hypothesis is not None:
            market_items.append(
                day_live_item(
                    "day.regime", "day_theme", "Current market regime", task.current_hypothesis, task.updated_at, source
                )
            )
    shadows = tuple(item for item in versions if item.deployment_state == "shadow")
    market_items.extend(
        _version_item(f"day.shadow.{index}", "Shadow Challenger", item, source)
        for index, item in enumerate(shadows, start=1)
    )
    return market_items


def _lead_market_items(actionable: tuple[UsDayTradeThesis, ...], source: str) -> tuple[WorkspaceItemV2, ...]:
    if not actionable:
        return ()
    lead = actionable[0]
    return (
        day_live_item(
            "day.theme.1", "day_theme", "Current Day theme", f"{lead.theme_name} · leading", lead.observed_at, source
        ),
        day_live_item(
            "day.leader.1", "day_theme", "Current Day leader", f"{lead.symbol} · leader", lead.observed_at, source
        ),
    )


def _close_report_items(reports: tuple[MarketCloseReport, ...], source: str) -> tuple[WorkspaceItemV2, ...]:
    return tuple(
        day_live_item(
            f"day.close_review.{index}", "paper", "Day close review", "finalized", report.payload.finalized_at, source
        )
        for index, report in enumerate(
            (item for item in reports if item.payload.market_id.value == "us_equities"), start=1
        )
    )


def _task(reader: DayAgentTaskReader | None, task_id: str) -> DayAgentResearchTask | None:
    return None if reader is None else reader.task(task_id)


def _version_item(item_id: str, label: str, version: DayAgentVersionView, source: str) -> WorkspaceItemV2:
    return day_live_item(item_id, "day_agent_version", label, version.version_id[:12], version.observed_at, source)


def _trace_graph(
    source: str, observed_at: dt.datetime, safe_ref: str, blocker_code: str | None, now: dt.datetime
) -> tuple[tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    nodes = (
        day_live_node(source, "source_receipt", "Day canonical readers", observed_at, safe_ref, "accepted"),
        day_live_node(
            "trace.day.decision", "reviewer_decision", "Day thesis decision", observed_at, safe_ref, "accepted"
        ),
        day_live_node("trace.day.paper", "paper_receipt", "Day Paper lifecycle", observed_at, safe_ref, "accepted"),
        *(
            ()
            if blocker_code is None
            else (
                day_live_node(
                    "trace.day.version_blocked",
                    "blocker_terminal",
                    "Day version source blocked",
                    now,
                    safe_ref,
                    "blocked",
                ),
            )
        ),
    )
    edges = (
        TraceEdgeV2(from_node_id=source, to_node_id="trace.day.decision", kind="reviewed_by"),
        TraceEdgeV2(from_node_id=source, to_node_id="trace.day.paper", kind="executed_as"),
        *(
            ()
            if blocker_code is None
            else (TraceEdgeV2(from_node_id=source, to_node_id="trace.day.version_blocked", kind="blocked_by"),)
        ),
    )
    return nodes, edges


__all__ = ("DayLiveProjection", "DayLiveReaders", "render_us_day_live")
