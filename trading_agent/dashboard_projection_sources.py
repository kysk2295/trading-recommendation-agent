from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceCapabilityV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_provider_alpaca import read_alpaca_provider
from trading_agent.dashboard_provider_evidence import ProviderEvidence
from trading_agent.dashboard_provider_kr import (
    read_kis_provider,
    read_ls_provider,
    read_opendart_provider,
)
from trading_agent.dashboard_provider_macro import (
    read_cftc_provider,
    read_fred_provider,
    read_treasury_provider,
)


def project_data_sources(
    outputs: Path,
    *,
    now: dt.datetime,
) -> tuple[WorkspaceProjection, tuple[SourceCapabilityV2, ...]]:
    evidence = (
        read_fred_provider(outputs, "fred", now),
        read_fred_provider(outputs, "alfred", now),
        read_treasury_provider(outputs, now),
        read_cftc_provider(outputs, now),
        read_opendart_provider(outputs, now),
        read_kis_provider(outputs, now),
        read_ls_provider(outputs, now),
        read_alpaca_provider(outputs, now),
    )
    parts = tuple(_project_provider(item, now) for item in evidence)
    accepted = all(item.blocker_code is None for item in evidence)
    root_id = "trace.data_sources.authorities"
    root_ref = hashlib.sha256("".join(item.safe_ref for item in evidence).encode()).hexdigest()
    root_nodes = (
        _node(root_id, "source_receipt", now, root_ref, "accepted", "data_sources"),
        *(
            ()
            if accepted
            else (
                _node(
                    f"{root_id}.blocker",
                    "blocker_terminal",
                    now,
                    root_ref,
                    "blocked",
                    "data_sources",
                ),
            )
        ),
    )
    root_edges = (
        ()
        if accepted
        else (
            TraceEdgeV2(
                from_node_id=root_id,
                to_node_id=f"{root_id}.blocker",
                kind="blocked_by",
            ),
        )
    )
    workspace = SourceStateV2(
        state="populated" if accepted else "blocked",
        observed_at=max(
            (item.observed_at for item in evidence if item.observed_at is not None),
            default=now,
        ),
        freshness=FreshnessV2(
            policy_id="provider-specific-authority-v2",
            age_seconds=0,
            as_of=now,
        ),
        blocker_code=None if accepted else "source_coverage_incomplete",
        summary="Provider-specific authoritative evidence projected",
        total_count=len(evidence),
        projected_count=len(evidence),
        truncated=False,
        trace_id=root_id,
        items=tuple(part[1] for part in parts),
    )
    return (
        WorkspaceProjection(
            workspace,
            tuple(node for part in parts for node in part[2]) + root_nodes,
            tuple(edge for part in parts for edge in part[3]) + root_edges,
        ),
        tuple(part[0] for part in parts),
    )


def _project_provider(
    evidence: ProviderEvidence,
    now: dt.datetime,
) -> tuple[
    SourceCapabilityV2,
    WorkspaceItemV2,
    tuple[TraceNodeV2, ...],
    tuple[TraceEdgeV2, ...],
]:
    source_id = f"trace.data_sources.{evidence.provider}"
    capability = SourceCapabilityV2(
        capability_id=f"{evidence.provider}.authoritative",
        provider=evidence.provider,
        label=evidence.provider.upper(),
        state=evidence.state,
        entitlement=evidence.entitlement,
        observed_at=evidence.observed_at,
        trace_id=source_id,
    )
    item = WorkspaceItemV2(
        item_id=f"source.{evidence.provider}",
        kind="metric",
        label=evidence.provider.upper(),
        state=evidence.state,
        value=evidence.value,
        observed_at=evidence.observed_at,
        trace_id=source_id,
    )
    source = _node(
        source_id,
        "source_receipt",
        evidence.observed_at or now,
        evidence.safe_ref,
        "unavailable" if evidence.observed_at is None else "accepted",
        evidence.provider,
    )
    if evidence.blocker_code is None:
        return capability, item, (source,), ()
    blocker_id = f"{source_id}.blocker"
    blocker = _node(
        blocker_id,
        "blocker_terminal",
        evidence.observed_at or now,
        evidence.safe_ref,
        "blocked",
        evidence.provider,
    )
    return (
        capability,
        item,
        (source, blocker),
        (TraceEdgeV2(from_node_id=source_id, to_node_id=blocker_id, kind="blocked_by"),),
    )


def _node(
    node_id: str,
    kind: Literal["source_receipt", "blocker_terminal"],
    observed_at: dt.datetime,
    safe_ref: str,
    state: Literal["accepted", "blocked", "unavailable"],
    namespace: str,
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label="Provider-specific typed authority",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace=f"provider.{namespace}",
    )


__all__ = ("project_data_sources",)
