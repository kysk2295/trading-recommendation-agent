from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Final, Literal, assert_never

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceCapabilityV2,
    SourceStateName,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.data_capability_models import (
    DataCapability,
    DataEntitlement,
    DataHealthState,
    DataSourceId,
    RedistributionPolicy,
)
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)

ProviderName = Literal["fred", "alfred", "treasury", "cftc", "opendart", "kis", "ls", "alpaca"]
PROVIDER_SOURCES: Final[dict[ProviderName, DataSourceId]] = {
    "fred": DataSourceId(provider="fred", feed="series_observations"),
    "alfred": DataSourceId(provider="alfred", feed="vintage_observations"),
    "treasury": DataSourceId(provider="treasury", feed="yield_curve"),
    "cftc": DataSourceId(provider="cftc", feed="tff"),
    "opendart": DataSourceId(provider="opendart", feed="list"),
    "kis": DataSourceId(provider="kis", feed="kr_ranking"),
    "ls": DataSourceId(provider="ls", feed="nws"),
    "alpaca": DataSourceId(provider="alpaca", feed="sip"),
}


def project_data_sources(
    outputs: Path,
    *,
    now: dt.datetime,
) -> tuple[WorkspaceProjection, tuple[SourceCapabilityV2, ...]]:
    path = outputs / "source_evidence" / "data_capability_registry.sqlite3"
    try:
        snapshot = DataCapabilityRegistryStore(path).snapshot(
            as_of=now,
            source_ids=tuple(
                sorted(PROVIDER_SOURCES.values(), key=lambda item: item.canonical_id)
            ),
        )
    except DataCapabilityRegistryError:
        return _missing_sources(now)
    capability_by_id = {item.source_id.canonical_id: item for item in snapshot.capabilities}
    entitlement_by_id = {item.source_id.canonical_id: item for item in snapshot.entitlements}
    projected = tuple(
        _provider_projection(
            provider,
            source,
            capability_by_id.get(source.canonical_id),
            entitlement_by_id.get(source.canonical_id),
            now,
        )
        for provider, source in PROVIDER_SOURCES.items()
    )
    states = tuple(item[0].state for item in projected)
    complete = all(state in {"populated", "empty"} for state in states)
    root_id = "trace.data_sources.registry"
    blocker_id = "trace.data_sources.coverage"
    registry_ref = hashlib.sha256(
        "".join(item[0].capability_id + item[0].state for item in projected).encode()
    ).hexdigest()
    root_nodes = (
        _node(root_id, "source_receipt", now, registry_ref, "accepted"),
        *(() if complete else (_node(blocker_id, "blocker_terminal", now, registry_ref, "blocked"),)),
    )
    root_edges = (
        ()
        if complete
        else (TraceEdgeV2(from_node_id=root_id, to_node_id=blocker_id, kind="blocked_by"),)
    )
    items = tuple(item for _, item, _, _ in projected)
    workspace = SourceStateV2(
        state="populated" if complete else "blocked",
        observed_at=max(
            (item.observed_at for item, _, _, _ in projected if item.observed_at is not None),
            default=now,
        ),
        freshness=FreshnessV2(policy_id="provider-capability-registry-v1", age_seconds=0, as_of=now),
        blocker_code=None if complete else "source_coverage_incomplete",
        summary="Provider capability registry projected",
        total_count=len(items),
        projected_count=len(items),
        truncated=False,
        trace_id=root_id,
        items=items,
    )
    return (
        WorkspaceProjection(
            workspace,
            tuple(node for _, _, nodes, _ in projected for node in nodes) + root_nodes,
            tuple(edge for _, _, _, edges in projected for edge in edges) + root_edges,
        ),
        tuple(item for item, _, _, _ in projected),
    )


def _provider_projection(
    provider: ProviderName,
    source: DataSourceId,
    capability: DataCapability | None,
    entitlement: DataEntitlement | None,
    now: dt.datetime,
) -> tuple[
    SourceCapabilityV2,
    WorkspaceItemV2,
    tuple[TraceNodeV2, ...],
    tuple[TraceEdgeV2, ...],
]:
    source_id = f"trace.data_sources.{provider}"
    if capability is None or entitlement is None:
        blocker_id = f"{source_id}.blocker"
        safe_ref = hashlib.sha256(source.canonical_id.encode()).hexdigest()
        return (
            SourceCapabilityV2(
                capability_id=f"{provider}.dashboard",
                provider=provider,
                label=provider.upper(),
                state="unavailable",
                entitlement="unavailable",
                observed_at=None,
                trace_id=source_id,
            ),
            WorkspaceItemV2(
                item_id=f"source.{provider}",
                kind="metric",
                label=provider.upper(),
                state="unavailable",
                value=None,
                observed_at=None,
                trace_id=source_id,
            ),
            (
                _node(source_id, "source_receipt", now, safe_ref, "unavailable"),
                _node(blocker_id, "blocker_terminal", now, safe_ref, "blocked"),
            ),
            (TraceEdgeV2(from_node_id=source_id, to_node_id=blocker_id, kind="blocked_by"),),
        )
    age = max(0, int((now - capability.assessed_at).total_seconds()))
    state = _capability_state(capability, age)
    safe_ref = hashlib.sha256(
        (capability.model_dump_json() + entitlement.model_dump_json()).encode()
    ).hexdigest()
    return (
        SourceCapabilityV2(
            capability_id=f"{provider}.dashboard",
            provider=provider,
            label=provider.upper(),
            state=state,
            entitlement=_entitlement(entitlement),
            observed_at=capability.assessed_at,
            trace_id=source_id,
        ),
        WorkspaceItemV2(
            item_id=f"source.{provider}",
            kind="metric",
            label=provider.upper(),
            state=state,
            value=capability.health_state.value,
            observed_at=capability.assessed_at,
            trace_id=source_id,
        ),
        (_node(source_id, "source_receipt", capability.assessed_at, safe_ref, "accepted"),),
        (),
    )


def _capability_state(capability: DataCapability, age: int) -> SourceStateName:
    if age > capability.freshness_slo_seconds:
        return "stale"
    match capability.health_state:
        case DataHealthState.COMPLETE:
            return "populated"
        case DataHealthState.DEGRADED:
            return "stale"
        case DataHealthState.INCOMPLETE:
            return "blocked"
        case DataHealthState.FAILED:
            return "error"
        case unreachable:
            assert_never(unreachable)


def _entitlement(
    entitlement: DataEntitlement,
) -> Literal["realtime", "delayed", "research_only", "unavailable"]:
    if entitlement.real_time and entitlement.redistribution_policy is not RedistributionPolicy.NONE:
        return "realtime"
    if entitlement.real_time:
        return "research_only"
    return "research_only" if entitlement.historical else "unavailable"


def _missing_sources(
    now: dt.datetime,
) -> tuple[WorkspaceProjection, tuple[SourceCapabilityV2, ...]]:
    projected = tuple(
        _provider_projection(provider, source, None, None, now)
        for provider, source in PROVIDER_SOURCES.items()
    )
    root_id = "trace.data_sources.registry"
    blocker_id = f"{root_id}.blocker"
    safe_ref = hashlib.sha256(b"data-capability-registry-missing").hexdigest()
    workspace = SourceStateV2(
        state="unavailable",
        observed_at=None,
        freshness=FreshnessV2(policy_id="provider-capability-registry-v1", age_seconds=None, as_of=now),
        blocker_code="source_capability_registry_missing",
        summary="Provider capability registry unavailable",
        total_count=8,
        projected_count=8,
        truncated=False,
        trace_id=root_id,
        items=tuple(item for _, item, _, _ in projected),
    )
    nodes = tuple(node for _, _, provider_nodes, _ in projected for node in provider_nodes)
    edges = tuple(edge for _, _, _, provider_edges in projected for edge in provider_edges)
    return (
        WorkspaceProjection(
            workspace,
            (
                *nodes,
                _node(root_id, "source_receipt", now, safe_ref, "unavailable"),
                _node(blocker_id, "blocker_terminal", now, safe_ref, "blocked"),
            ),
            (
                *edges,
                TraceEdgeV2(from_node_id=root_id, to_node_id=blocker_id, kind="blocked_by"),
            ),
        ),
        tuple(item for item, _, _, _ in projected),
    )


def _node(
    node_id: str,
    kind: Literal["source_receipt", "blocker_terminal"],
    observed_at: dt.datetime,
    safe_ref: str,
    state: Literal["accepted", "blocked", "unavailable"],
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label="Provider capability evidence",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace="data.capability_registry",
    )
