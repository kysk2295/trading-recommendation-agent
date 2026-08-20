from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from trading_agent.dashboard_models_v2 import SourceStateV2, TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.day_agent_task_models import DayAgentResearchTask
from trading_agent.day_agent_task_store import DayAgentTaskReader, DayAgentTaskStore, DayAgentTaskStoreError
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_learning_report_store import InvalidDayLearningReportError, load_market_close_report
from trading_agent.execution_ledger_reader import ReconciliationLedger, read_reconciliation_ledger
from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_models import HermesDeliveryEvent, HermesDeliveryKind
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.paper_execution_models import IntentId
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayThesisChange, UsDayTradeThesis
from trading_agent.us_day_thesis_store import InvalidUsDayThesisStoreError, UsDayThesisStore

_STALE_AFTER = dt.timedelta(minutes=15)


class DayLiveSourceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DayAgentVersionView:
    version_id: str
    deployment_state: Literal["champion", "shadow"]
    task_id: str
    observed_at: dt.datetime


class DayAgentVersionReader(Protocol):
    def versions(self) -> tuple[DayAgentVersionView, ...]: ...


class DayCloseReportReader(Protocol):
    def reports(self) -> tuple[MarketCloseReport, ...]: ...


@dataclass(frozen=True, slots=True)
class _EmptyVersionReader:
    def versions(self) -> tuple[DayAgentVersionView, ...]:
        return ()


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


@dataclass(frozen=True, slots=True)
class _Readers:
    theses: tuple[UsDayTradeThesis, ...]
    changes: dict[str, tuple[UsDayThesisChange, ...]]
    task_reader: DayAgentTaskReader | None
    version_reader: DayAgentVersionReader
    close_reports: DayCloseReportReader
    hermes_events: tuple[HermesDeliveryEvent, ...]
    ledger: ReconciliationLedger | None


@dataclass(frozen=True, slots=True)
class DayLiveProjection:
    markets: tuple[WorkspaceItemV2, ...]
    paper: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def project_us_day_live(
    outputs: Path,
    *,
    now: dt.datetime,
    version_reader: DayAgentVersionReader | None = None,
    paper_finalized: bool = False,
) -> DayLiveProjection:
    try:
        readers = _canonical_readers(outputs, version_reader)
        reports = readers.close_reports.reports()
    except (
        DayLiveSourceError,
        DayAgentTaskStoreError,
        InvalidHermesDeliveryStoreError,
        InvalidUsDayThesisStoreError,
        OSError,
        ValueError,
    ):
        return _blocked(now)
    if readers.theses and now - max(item.observed_at for item in readers.theses) > _STALE_AFTER:
        return _blocked(now, state="blocked", value="stale")
    return _accepted(readers, reports, now, paper_finalized)


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


def _canonical_readers(outputs: Path, version_reader: DayAgentVersionReader | None) -> _Readers:
    root = outputs / "us_day"
    thesis_root = root / "theses"
    theses = () if not thesis_root.exists() else UsDayThesisStore(thesis_root).theses()
    changes = {item.thesis_id: UsDayThesisStore(thesis_root).changes(item.thesis_id) for item in theses}
    task_path = root / "day_agent.sqlite3"
    task_reader = DayAgentTaskStore(task_path).reader() if task_path.exists() else None
    hermes_path = outputs / "hermes" / "delivery.sqlite3"
    events = HermesDeliveryReader(hermes_path).events() if hermes_path.exists() else ()
    ledger_path = outputs / "paper" / "execution.sqlite3"
    ledger = read_reconciliation_ledger(ledger_path) if ledger_path.exists() else None
    return _Readers(
        theses,
        changes,
        task_reader,
        _EmptyVersionReader() if version_reader is None else version_reader,
        _ImmutableCloseReportReader(root / "close_reports"),
        events,
        ledger,
    )


def _accepted(
    readers: _Readers,
    reports: tuple[MarketCloseReport, ...],
    now: dt.datetime,
    paper_finalized: bool,
) -> DayLiveProjection:
    source = "trace.day.source"
    ordered = tuple(sorted(readers.theses, key=lambda item: (item.observed_at, item.thesis_id), reverse=True))
    versions = tuple(
        sorted(readers.version_reader.versions(), key=lambda item: (item.observed_at, item.version_id), reverse=True)
    )
    market_items: list[WorkspaceItemV2] = []
    champion = next((item for item in versions if item.deployment_state == "champion"), None)
    if champion is not None:
        market_items.append(_version_item("day.champion", "Current Champion", champion, source))
        task = _task(readers.task_reader, champion.task_id)
        if task is not None and task.current_hypothesis is not None:
            market_items.append(
                _item(
                    "day.regime", "day_theme", "Current market regime", task.current_hypothesis, task.updated_at, source
                )
            )
    shadows = tuple(item for item in versions if item.deployment_state == "shadow")
    market_items.extend(
        _version_item(f"day.shadow.{index}", "Shadow Challenger", item, source)
        for index, item in enumerate(shadows, start=1)
    )
    actionable = tuple(item for item in ordered if item.decision is DayTradeDecision.RECOMMEND)
    if actionable:
        lead = actionable[0]
        market_items.extend(
            (
                _item(
                    "day.theme.1",
                    "day_theme",
                    "Current Day theme",
                    f"{lead.theme_name} · leading",
                    lead.observed_at,
                    source,
                ),
                _item(
                    "day.leader.1",
                    "day_theme",
                    "Current Day leader",
                    f"{lead.symbol} · leader",
                    lead.observed_at,
                    source,
                ),
            )
        )
    paper_items: list[WorkspaceItemV2] = []
    terminal_index = 0
    for thesis in actionable + tuple(item for item in ordered if item.decision is not DayTradeDecision.RECOMMEND):
        if thesis.decision is not DayTradeDecision.RECOMMEND:
            terminal_index += 1
        market_items.extend(_thesis_items(thesis, readers.changes[thesis.thesis_id], terminal_index, source))
        if thesis.decision is DayTradeDecision.RECOMMEND and readers.ledger is not None and paper_finalized:
            paper_items.extend(_paper_items(thesis, readers.ledger, readers.hermes_events, source))
    close_index = 0
    for report in reports:
        if report.payload.market_id.value != "us_equities":
            continue
        close_index += 1
        paper_items.append(
            _item(
                f"day.close_review.{close_index}",
                "paper",
                "Day close review",
                "finalized",
                report.payload.finalized_at,
                source,
            )
        )
    safe_ref = hashlib.sha256(":".join(item.thesis_id for item in ordered).encode()).hexdigest()
    observed_at = max((item.observed_at for item in (*ordered, *versions)), default=now)
    nodes = (
        _node(source, "source_receipt", "Day canonical readers", observed_at, safe_ref, "accepted"),
        _node("trace.day.decision", "reviewer_decision", "Day thesis decision", observed_at, safe_ref, "accepted"),
        _node("trace.day.paper", "paper_receipt", "Day Paper lifecycle", observed_at, safe_ref, "accepted"),
    )
    edges = (
        TraceEdgeV2(from_node_id=source, to_node_id="trace.day.decision", kind="reviewed_by"),
        TraceEdgeV2(from_node_id=source, to_node_id="trace.day.paper", kind="executed_as"),
    )
    return DayLiveProjection(tuple(market_items), tuple(paper_items), nodes, edges)


def _task(reader: DayAgentTaskReader | None, task_id: str) -> DayAgentResearchTask | None:
    return None if reader is None else reader.task(task_id)


def _version_item(item_id: str, label: str, version: DayAgentVersionView, source: str) -> WorkspaceItemV2:
    return _item(item_id, "day_agent_version", label, version.version_id[:12], version.observed_at, source)


def _thesis_items(
    thesis: UsDayTradeThesis, changes: tuple[UsDayThesisChange, ...], index: int, source: str
) -> tuple[WorkspaceItemV2, ...]:
    if thesis.decision is DayTradeDecision.RECOMMEND:
        assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
        targets = "/".join(str(item.price) for item in thesis.targets)
        items = [
            _item(
                f"day.recommendation.{thesis.symbol}",
                "day_recommendation",
                f"{thesis.symbol} active thesis",
                f"entry {thesis.entry_price} · stop {thesis.stop_price} · targets {targets}",
                thesis.observed_at,
                source,
            )
        ]
        if changes:
            change = max(changes, key=lambda item: (item.occurred_at, item.event_id))
            items.append(
                _item(
                    f"day.thesis_change.{thesis.symbol}",
                    "day_recommendation",
                    f"{thesis.symbol} thesis change",
                    change.kind.value,
                    change.occurred_at,
                    source,
                )
            )
        return tuple(items)
    prefix = "day.no_trade" if thesis.decision is DayTradeDecision.NO_TRADE else "day.terminal"
    value = (
        f"NO_TRADE · {thesis.reason_code}"
        if thesis.decision is DayTradeDecision.NO_TRADE
        else f"{thesis.decision.value.upper()} · {thesis.reason_code}"
    )
    return (
        _item(f"{prefix}.{index}", "day_recommendation", "Day terminal decision", value, thesis.observed_at, source),
    )


def _paper_items(
    thesis: UsDayTradeThesis, ledger: ReconciliationLedger, events: tuple[HermesDeliveryEvent, ...], source: str
) -> tuple[WorkspaceItemV2, ...]:
    assert thesis.symbol is not None
    intent_id = IntentId(thesis.thesis_id)
    state = next((item for item in ledger.order_states if item.intent_id == intent_id), None)
    if state is None:
        return ()
    values = ["filled" if intent_id in ledger.filled_intent_ids else "submitted"]
    protected = any(item.plan.parent_intent_id == intent_id for item in ledger.protective_oco_plans)
    if protected:
        values.append("protected")
    if intent_id not in ledger.unresolved_intent_ids:
        values.append("reconciled")
    observed_at = thesis.observed_at
    item = _item(
        f"day.paper.{thesis.symbol}",
        "paper",
        f"{thesis.symbol} Paper lifecycle",
        " · ".join(values),
        observed_at,
        source,
    )
    exits = tuple(
        event
        for event in events
        if event.kind is HermesDeliveryKind.EXIT and f"intent:{thesis.thesis_id}" in event.evidence_refs
    )
    if not exits:
        return (item,)
    exit_event = max(exits, key=lambda event: (event.occurred_at, event.delivery_id))
    return (
        item,
        _item(
            f"day.paper_exit.{thesis.symbol}",
            "paper",
            f"{thesis.symbol} Paper exit",
            "closed",
            exit_event.occurred_at,
            source,
        ),
    )


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
        _node(source, "source_receipt", "Day live source", now, safe_ref, "unavailable"),
        _node("trace.day.blocker", "blocker_terminal", "Day live source blocked", now, safe_ref, "blocked"),
    )
    return DayLiveProjection(
        (item,),
        (item.model_copy(update={"item_id": "day.paper_source"}),),
        nodes,
        (TraceEdgeV2(from_node_id=source, to_node_id="trace.day.blocker", kind="blocked_by"),),
    )


def _item(
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


def _node(
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


__all__ = (
    "DayAgentVersionReader",
    "DayAgentVersionView",
    "DayCloseReportReader",
    "DayLiveProjection",
    "DayLiveSourceError",
    "merge_us_day_live",
    "project_us_day_live",
)
