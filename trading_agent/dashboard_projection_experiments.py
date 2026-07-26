from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection, blocked_projection
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)


def project_research(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    reader = ExperimentLedgerReader(
        outputs / "experiment_control" / "experiment_ledger.sqlite3"
    )
    if not reader.is_initialized():
        return blocked_projection(
            "research",
            now=now,
            state="unavailable",
            blocker_code="research_catalog_missing",
        )
    try:
        sources = reader.research_sources()
        hypotheses = reader.hypotheses()
    except (InvalidExperimentLedgerSourceError, UnsupportedExperimentLedgerSchemaError):
        return blocked_projection(
            "research",
            now=now,
            state="corrupt",
            blocker_code="research_lineage_invalid",
        )
    observed = tuple(
        [source.source.ledger_recorded_at for source in sources]
        + [hypothesis.registration.ledger_recorded_at for hypothesis in hypotheses]
    )
    if any(item > now + dt.timedelta(minutes=5) for item in observed):
        return blocked_projection(
            "research",
            now=now,
            state="corrupt",
            blocker_code="research_future_observation",
        )
    rows = tuple(
        (
            f"research.source.{index}",
            source.source.source_kind.value,
            source.source.source_id,
            source.source.ledger_recorded_at,
            str(source.source_key),
        )
        for index, source in enumerate(sources)
    ) + tuple(
        (
            f"research.hypothesis.{index}",
            hypothesis.registration.primary_lane.value,
            hypothesis.registration.hypothesis_id,
            hypothesis.registration.ledger_recorded_at,
            str(hypothesis.registration_key),
        )
        for index, hypothesis in enumerate(hypotheses)
    )
    return _accepted("research", rows, now, "reviewer_decision")


def project_strategies(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    reader = ExperimentLedgerReader(
        outputs / "experiment_control" / "experiment_ledger.sqlite3"
    )
    if not reader.is_initialized():
        return blocked_projection(
            "strategies",
            now=now,
            state="unavailable",
            blocker_code="lane_registry_missing",
        )
    try:
        versions = reader.strategy_versions()
        lifecycle = tuple(
            (version, reader.lifecycle_events(version.registration.strategy_version))
            for version in versions
        )
    except (InvalidExperimentLedgerSourceError, UnsupportedExperimentLedgerSchemaError):
        return blocked_projection(
            "strategies",
            now=now,
            state="corrupt",
            blocker_code="trial_binding_invalid",
        )
    if any(
        event.event.decided_at > now + dt.timedelta(minutes=5)
        for _, events in lifecycle
        for event in events
    ):
        return blocked_projection(
            "strategies",
            now=now,
            state="corrupt",
            blocker_code="lifecycle_conflict",
        )
    if any(not events for _, events in lifecycle):
        return blocked_projection(
            "strategies",
            now=now,
            state="blocked",
            blocker_code="reviewer_missing",
        )
    rows = tuple(
        (
            f"strategy.version.{index}",
            version.registration.lane_id.value,
            events[-1].event.to_state.value,
            events[-1].event.decided_at,
            str(events[-1].event_key),
        )
        for index, (version, events) in enumerate(lifecycle)
    )
    return _accepted("strategies", rows, now, "lifecycle_decision")


def _accepted(
    workspace: Literal["research", "strategies"],
    rows: tuple[tuple[str, str, str, dt.datetime, str], ...],
    now: dt.datetime,
    terminal_kind: Literal["reviewer_decision", "lifecycle_decision"],
) -> WorkspaceProjection:
    capped = rows[:24]
    root_id = f"trace.{workspace}.ledger"
    terminal_id = f"{root_id}.terminal"
    root_ref = hashlib.sha256(workspace.encode()).hexdigest()
    nodes: list[TraceNodeV2] = [
        _node(root_id, "source_receipt", now, root_ref, workspace),
        _node(terminal_id, terminal_kind, now, root_ref, workspace),
    ]
    edges: list[TraceEdgeV2] = [
        TraceEdgeV2(
            from_node_id=root_id,
            to_node_id=terminal_id,
            kind="reviewed_by" if workspace == "research" else "decided_by",
        )
    ]
    items: list[WorkspaceItemV2] = []
    for item_id, label, value, observed_at, reference in capped:
        source_id = f"trace.{item_id}"
        item_terminal = f"{source_id}.terminal"
        safe_ref = (
            reference
            if len(reference) == 64 and all(character in "0123456789abcdef" for character in reference)
            else hashlib.sha256(reference.encode()).hexdigest()
        )
        nodes.extend(
            (
                _node(source_id, "source_receipt", observed_at, safe_ref, workspace),
                _node(item_terminal, terminal_kind, observed_at, safe_ref, workspace),
            )
        )
        edges.append(
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=item_terminal,
                kind="reviewed_by" if workspace == "research" else "decided_by",
            )
        )
        items.append(
            WorkspaceItemV2(
                item_id=item_id,
                kind="research" if workspace == "research" else "strategy",
                label=label[:80],
                state="populated",
                value=value[:160],
                observed_at=observed_at,
                trace_id=source_id,
            )
        )
    total = len(rows)
    return WorkspaceProjection(
        SourceStateV2(
            state="populated" if rows else "empty",
            observed_at=max((row[3] for row in rows), default=now),
            freshness=FreshnessV2(policy_id=f"{workspace}-ledger-v1", age_seconds=0, as_of=now),
            blocker_code=None,
            summary=f"Authoritative {workspace} ledger projected",
            total_count=total,
            projected_count=len(items),
            truncated=total > len(items),
            trace_id=root_id,
            items=tuple(items),
        ),
        tuple(nodes),
        tuple(edges),
    )


def _node(
    node_id: str,
    kind: Literal["source_receipt", "reviewer_decision", "lifecycle_decision"],
    observed_at: dt.datetime,
    safe_ref: str,
    workspace: str,
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=f"{workspace} immutable ledger evidence",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state="accepted",
        source_namespace=f"experiment_ledger.{workspace}",
    )
