from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from trading_agent.dashboard_models_v2 import SourceStateV2, TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.us_day_thesis_models import DayTradeDecision, ThesisChangeKind, UsDayThesisChange, UsDayTradeThesis

_STALE_AFTER = dt.timedelta(minutes=15)


class DayLiveSourceError(ValueError):
    pass


class _AgentVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    deployment_state: Literal["champion", "shadow"]
    observed_at: AwareDatetime


class _CloseReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    thesis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: AwareDatetime
    status: Literal["reviewed", "pending", "blocked"]


class _MarketRegime(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    observed_at: AwareDatetime


@dataclass(frozen=True, slots=True)
class DayLiveProjection:
    markets: tuple[WorkspaceItemV2, ...]
    paper: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def project_us_day_live(outputs: Path, *, now: dt.datetime) -> DayLiveProjection:
    root = outputs / "us_day"
    if not root.exists():
        return DayLiveProjection((), (), (), ())
    try:
        _require_private_directory(root)
        theses = _theses(root / "theses")
        changes = {thesis.thesis_id: _changes(root / "theses", thesis) for thesis in theses}
        versions = _optional_models(root / "agent_versions.json", _AgentVersion)
        reviews = _optional_models(root / "close_reviews.json", _CloseReview)
        regimes = _optional_models(root / "market_regime.json", _MarketRegime)
        _read_hermes(outputs / "hermes" / "delivery.sqlite3")
    except (DayLiveSourceError, InvalidHermesDeliveryStoreError, ValidationError):
        return _blocked(now)
    observed = tuple(thesis.observed_at for thesis in theses)
    if observed and now - max(observed) > _STALE_AFTER:
        return _blocked(now, state="blocked", value="stale")
    return _accepted(theses, changes, versions, reviews, regimes, now)


def merge_us_day_live(
    base: WorkspaceProjection, day: DayLiveProjection, *, workspace: Literal["markets", "paper"]
) -> WorkspaceProjection:
    items = day.markets if workspace == "markets" else day.paper
    available = max(0, 24 - len(base.workspace.items))
    projected_items = items[:available]
    total = base.workspace.total_count + len(items)
    projected = len(base.workspace.items) + len(projected_items)
    merged = SourceStateV2(
        **base.workspace.model_dump(exclude={"total_count", "projected_count", "truncated", "items"}),
        total_count=total,
        projected_count=projected,
        truncated=total > projected,
        items=(*base.workspace.items, *projected_items),
    )
    if workspace == "markets":
        return WorkspaceProjection(merged, (*base.nodes, *day.nodes), (*base.edges, *day.edges))
    return WorkspaceProjection(merged, base.nodes, base.edges)


def _accepted(
    theses: tuple[UsDayTradeThesis, ...],
    changes: dict[str, tuple[UsDayThesisChange, ...]],
    versions: tuple[_AgentVersion, ...],
    reviews: tuple[_CloseReview, ...],
    regimes: tuple[_MarketRegime, ...],
    now: dt.datetime,
) -> DayLiveProjection:
    source = "trace.day.source"
    terminal = "trace.day.decision"
    ordered = tuple(
        sorted(theses, key=lambda item: (item.decision is DayTradeDecision.RECOMMEND, item.observed_at), reverse=True)
    )
    actionable = tuple(item for item in ordered if item.decision is DayTradeDecision.RECOMMEND)
    market_items: list[WorkspaceItemV2] = []
    if regimes:
        regime = max(regimes, key=lambda item: item.observed_at)
        market_items.append(
            _item("day.regime", "day_theme", "Current market regime", regime.label, regime.observed_at, source)
        )
    if actionable:
        leader = actionable[0]
        market_items.extend(
            (
                _item(
                    "day.theme.1",
                    "day_theme",
                    "Current Day theme",
                    f"{leader.theme_name} · leading",
                    leader.observed_at,
                    source,
                ),
                _item(
                    "day.leader.1",
                    "day_theme",
                    "Current Day leader",
                    f"{leader.symbol} · leader",
                    leader.observed_at,
                    source,
                ),
            )
        )
    champion = next(
        (
            item
            for item in sorted(versions, key=lambda item: item.observed_at, reverse=True)
            if item.deployment_state == "champion"
        ),
        None,
    )
    if champion is None and actionable:
        market_items.append(
            _item(
                "day.champion",
                "day_agent_version",
                "Current Champion",
                actionable[0].agent_version_id[:12],
                actionable[0].observed_at,
                source,
            )
        )
    elif champion is not None:
        market_items.append(
            _item(
                "day.champion",
                "day_agent_version",
                "Current Champion",
                champion.version_id[:12],
                champion.observed_at,
                source,
            )
        )
    market_items.extend(
        _item(
            f"day.shadow.{index}",
            "day_agent_version",
            "Shadow Challenger",
            item.version_id[:12],
            item.observed_at,
            source,
        )
        for index, item in enumerate((item for item in versions if item.deployment_state == "shadow"), start=1)
    )
    paper_items: list[WorkspaceItemV2] = []
    terminal_index = 0
    for thesis in ordered:
        if thesis.decision is not DayTradeDecision.RECOMMEND:
            terminal_index += 1
        market_items.extend(_thesis_market_items(thesis, changes[thesis.thesis_id], terminal_index, source))
        if thesis.decision is DayTradeDecision.RECOMMEND:
            paper_items.append(_paper_item(thesis, changes[thesis.thesis_id], source))
            if changes[thesis.thesis_id] and changes[thesis.thesis_id][-1].kind is ThesisChangeKind.CLOSE:
                paper_items.append(
                    _item(
                        f"day.paper_exit.{thesis.symbol}",
                        "paper",
                        f"{thesis.symbol} Paper exit",
                        "closed",
                        changes[thesis.thesis_id][-1].occurred_at,
                        source,
                    )
                )
    for review in reviews:
        thesis = next((item for item in theses if item.thesis_id == review.thesis_id), None)
        if thesis is None or thesis.symbol is None:
            raise DayLiveSourceError
        paper_items.append(
            _item(
                f"day.close_review.{thesis.symbol}",
                "paper",
                "Day close review",
                review.status,
                review.observed_at,
                source,
            )
        )
    safe_ref = hashlib.sha256(":".join(item.thesis_id for item in ordered).encode()).hexdigest()
    observed_at = max((item.observed_at for item in (*theses, *versions, *reviews, *regimes)), default=now)
    nodes = (
        TraceNodeV2(
            node_id=source,
            kind="source_receipt",
            label="Day immutable artifacts",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="accepted",
            source_namespace="day.live",
        ),
        TraceNodeV2(
            node_id=terminal,
            kind="reviewer_decision",
            label="Day thesis decision",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="accepted",
            source_namespace="day.live",
        ),
        TraceNodeV2(
            node_id="trace.day.paper",
            kind="paper_receipt",
            label="Day Paper lifecycle",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="accepted",
            source_namespace="day.live",
        ),
    )
    return DayLiveProjection(
        tuple(market_items),
        tuple(paper_items),
        nodes,
        (
            TraceEdgeV2(from_node_id=source, to_node_id=terminal, kind="reviewed_by"),
            TraceEdgeV2(from_node_id=source, to_node_id="trace.day.paper", kind="executed_as"),
        ),
    )


def _thesis_market_items(
    thesis: UsDayTradeThesis, changes: tuple[UsDayThesisChange, ...], index: int, source: str
) -> tuple[WorkspaceItemV2, ...]:
    if thesis.decision is DayTradeDecision.RECOMMEND:
        assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
        targets = "/".join(str(item.price) for item in thesis.targets)
        result = [
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
            result.append(
                _item(
                    f"day.thesis_change.{thesis.symbol}",
                    "day_recommendation",
                    f"{thesis.symbol} thesis change",
                    changes[-1].kind.value,
                    changes[-1].occurred_at,
                    source,
                )
            )
        return tuple(result)
    if thesis.decision is DayTradeDecision.NO_TRADE:
        return (
            _item(
                f"day.no_trade.{index}",
                "day_recommendation",
                "Day terminal decision",
                f"NO_TRADE · {thesis.reason_code}",
                thesis.observed_at,
                source,
            ),
        )
    return (
        _item(
            f"day.terminal.{index}",
            "day_recommendation",
            "Day terminal decision",
            f"{thesis.decision.value.upper()} · {thesis.reason_code}",
            thesis.observed_at,
            source,
        ),
    )


def _paper_item(thesis: UsDayTradeThesis, changes: tuple[UsDayThesisChange, ...], source: str) -> WorkspaceItemV2:
    assert thesis.symbol is not None
    notes = {item.note for item in changes}
    lifecycle = "filled" if "entry_acknowledged" in notes else "submitted"
    lifecycle += " · protected" if "protective_oco_acknowledged" in notes else " · unprotected"
    lifecycle += " · reconciled" if "reconciled" in notes else " · pending"
    observed_at = changes[-1].occurred_at if changes else thesis.observed_at
    return _item(
        f"day.paper.{thesis.symbol}", "paper", f"{thesis.symbol} Paper lifecycle", lifecycle, observed_at, source
    )


def _blocked(
    now: dt.datetime, *, state: Literal["blocked", "corrupt"] = "corrupt", value: str = "source invalid"
) -> DayLiveProjection:
    source = "trace.day.source"
    terminal = "trace.day.blocker"
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
        TraceNodeV2(
            node_id=source,
            kind="source_receipt",
            label="Day live source",
            observed_at=now,
            safe_ref=safe_ref,
            state="unavailable",
            source_namespace="day.live",
        ),
        TraceNodeV2(
            node_id=terminal,
            kind="blocker_terminal",
            label="Day live source blocked",
            observed_at=now,
            safe_ref=safe_ref,
            state="blocked",
            source_namespace="day.live",
        ),
    )
    return DayLiveProjection(
        (item,),
        (item.model_copy(update={"item_id": "day.paper_source"}),),
        nodes,
        (TraceEdgeV2(from_node_id=source, to_node_id=terminal, kind="blocked_by"),),
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


def _theses(root: Path) -> tuple[UsDayTradeThesis, ...]:
    files = _private_json_files(root / "theses")
    theses = tuple(UsDayTradeThesis.model_validate_json(_read_text(path)) for path in files)
    if any(path.stem != thesis.thesis_id for path, thesis in zip(files, theses, strict=True)):
        raise DayLiveSourceError
    return theses


def _changes(root: Path, thesis: UsDayTradeThesis) -> tuple[UsDayThesisChange, ...]:
    directory = root / "changes" / thesis.thesis_id
    if not directory.exists():
        return ()
    files = _private_json_files(directory)
    changes = tuple(UsDayThesisChange.model_validate_json(_read_text(path)) for path in files)
    if any(
        change.thesis_id != thesis.thesis_id or path.stem != change.event_id
        for path, change in zip(files, changes, strict=True)
    ):
        raise DayLiveSourceError
    return tuple(sorted(changes, key=lambda item: item.occurred_at))


def _optional_models[ModelT: BaseModel](path: Path, model: type[ModelT]) -> tuple[ModelT, ...]:
    if not path.exists():
        return ()
    try:
        return TypeAdapter(tuple[model, ...]).validate_json(_read_text(path))
    except (DayLiveSourceError, ValidationError, ValueError):
        raise DayLiveSourceError from None


def _private_json_files(directory: Path) -> tuple[Path, ...]:
    _require_private_directory(directory)
    try:
        return tuple(sorted(path for path in directory.iterdir() if path.suffix == ".json"))
    except OSError:
        raise DayLiveSourceError from None


def _require_private_directory(directory: Path) -> None:
    try:
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise DayLiveSourceError
    except OSError:
        raise DayLiveSourceError from None


def _read_text(path: Path) -> str:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise DayLiveSourceError
        content = os.read(descriptor, 65_537)
        after = os.fstat(descriptor)
        if (
            len(content) > 65_536
            or before.st_size != len(content)
            or (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns)
        ):
            raise DayLiveSourceError
        return content.decode("utf-8")
    except (OSError, UnicodeError):
        raise DayLiveSourceError from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_hermes(path: Path) -> None:
    if path.exists():
        _ = HermesDeliveryReader(path).events()


__all__ = ("DayLiveProjection", "DayLiveSourceError", "merge_us_day_live", "project_us_day_live")
