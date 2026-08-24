from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from trading_agent.dashboard_models_v2 import SourceStateV2, TraceEdgeV2, WorkspaceItemV2
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_projection_day_agent_us import read_us_day_paper_events
from trading_agent.dashboard_us_day_live_primitives import day_live_node
from trading_agent.dashboard_us_day_live_render import DayLiveProjection, DayLiveReaders, render_us_day_live
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle
from trading_agent.dashboard_us_day_versions import DayAgentVersionReader, DayAgentVersionView
from trading_agent.day_agent_task_store import DayAgentTaskStore, DayAgentTaskStoreError
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_learning_report_store import InvalidDayLearningReportError, load_market_close_report
from trading_agent.us_day_thesis_store import InvalidUsDayThesisStoreError, UsDayThesisStore

_STALE_AFTER = dt.timedelta(minutes=15)


class DayLiveSourceError(ValueError):
    pass


class DayCloseReportReader(Protocol):
    def reports(self) -> tuple[MarketCloseReport, ...]: ...


@dataclass(frozen=True, slots=True)
class _ImmutableCloseReportReader:
    root: Path

    def reports(self) -> tuple[MarketCloseReport, ...]:
        if not self.root.exists():
            return ()
        try:
            reports = tuple(load_market_close_report(path) for path in self.root.glob("market_close_report_*.json"))
        except (InvalidDayLearningReportError, OSError):
            raise DayLiveSourceError from None
        return tuple(sorted(reports, key=lambda item: (item.payload.finalized_at, item.report_id), reverse=True))


def project_us_day_live(
    outputs: Path,
    *,
    now: dt.datetime,
    version_reader: DayAgentVersionReader | None = None,
    paper_ledger: FinalizedPaperProjectionBundle | None = None,
) -> DayLiveProjection:
    try:
        readers, close_reports = _canonical_readers(outputs, version_reader)
        reports = close_reports.reports()
        if paper_ledger is not None and not paper_ledger.hermes_valid:
            return _blocked(now)
        if readers.theses and now - max(item.observed_at for item in readers.theses) > _STALE_AFTER:
            return _blocked(now, state="blocked", value="stale")
        return render_us_day_live(readers, reports, now, paper_ledger)
    except (
        DayLiveSourceError,
        DayAgentTaskStoreError,
        InvalidUsDayThesisStoreError,
        OSError,
        ValueError,
    ):
        return _blocked(now)


def merge_us_day_live(
    base: WorkspaceProjection, day: DayLiveProjection, *, workspace: Literal["markets", "paper"]
) -> WorkspaceProjection:
    items = day.markets if workspace == "markets" else day.paper
    kept = items[: max(0, 24 - len(base.workspace.items))]
    total = base.workspace.total_count + len(items)
    projected = len(base.workspace.items) + len(kept)
    merged = SourceStateV2(
        **base.workspace.model_dump(exclude={"total_count", "projected_count", "truncated", "items"}),
        total_count=total,
        projected_count=projected,
        truncated=total > projected,
        items=(*base.workspace.items, *kept),
    )
    return (
        WorkspaceProjection(merged, (*base.nodes, *day.nodes), (*base.edges, *day.edges))
        if workspace == "markets"
        else WorkspaceProjection(merged, base.nodes, base.edges)
    )


def _canonical_readers(
    outputs: Path, version_reader: DayAgentVersionReader | None
) -> tuple[DayLiveReaders, DayCloseReportReader]:
    root = outputs / "us_day"
    thesis_root = root / "theses"
    if not thesis_root.exists():
        theses = ()
        changes = {}
    else:
        thesis_store = UsDayThesisStore(thesis_root)
        theses = thesis_store.theses()
        changes = {item.thesis_id: thesis_store.changes(item.thesis_id) for item in theses}
    paper_events = read_us_day_paper_events(root / "paper.sqlite3", tuple(item.thesis_id for item in theses))
    task_path = root / "day_agent.sqlite3"
    task_reader = DayAgentTaskStore(task_path).reader() if task_path.exists() else None
    readers = DayLiveReaders(theses, changes, paper_events, task_reader, version_reader)
    return readers, _ImmutableCloseReportReader(root / "close_reports")


def _blocked(
    now: dt.datetime, *, state: Literal["blocked", "corrupt"] = "corrupt", value: str = "source invalid"
) -> DayLiveProjection:
    source = "trace.day.source"
    safe_ref = hashlib.sha256(value.encode()).hexdigest()
    item = WorkspaceItemV2(
        item_id="day.source",
        kind="system",
        label="Day live source",
        state=state,
        value=value,
        observed_at=now,
        trace_id=source,
    )
    nodes = (
        day_live_node(source, "source_receipt", "Day live source", now, safe_ref, "unavailable"),
        day_live_node("trace.day.blocker", "blocker_terminal", "Day live source blocked", now, safe_ref, "blocked"),
    )
    return DayLiveProjection(
        (item,),
        (item.model_copy(update={"item_id": "day.paper_source"}),),
        nodes,
        (TraceEdgeV2(from_node_id=source, to_node_id="trace.day.blocker", kind="blocked_by"),),
    )


__all__ = (
    "DayAgentVersionReader",
    "DayAgentVersionView",
    "DayCloseReportReader",
    "DayLiveProjection",
    "DayLiveSourceError",
    "merge_us_day_live",
    "project_us_day_live",
)
