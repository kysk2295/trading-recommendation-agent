from __future__ import annotations

import datetime as dt
import uuid
from typing import cast

from tests.dashboard_options_workbench_fixtures import empty_workbench_payload

type ModelInput = (
    str
    | int
    | bool
    | None
    | dt.datetime
    | uuid.UUID
    | list["ModelInput"]
    | tuple["ModelInput", ...]
    | dict[str, "ModelInput"]
)

_NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)
_TRACE_IDS = {
    "command_center": "trace-command",
    "overview": "trace-overview",
    "markets": "trace-markets",
    "data_sources": "trace-data",
    "research": "trace-research",
    "strategies": "trace-strategies",
    "derivatives": "trace-derivatives",
    "paper": "trace-paper",
    "system": "trace-system",
}


def snapshot_payload() -> dict[str, ModelInput]:
    workspaces: dict[str, ModelInput] = {name: _state(trace_id) for name, trace_id in _TRACE_IDS.items()}
    command = cast(dict[str, ModelInput], workspaces["command_center"])
    command["agents"] = [
        {
            "agent_id": "systematic_quant",
            "label": "Systematic Quant",
            "role": "systematic experiment research",
            "capabilities": ["conversation", "directed_tool", "autonomous_research"],
            "runtime_state": "idle",
            "trace_id": _TRACE_IDS["command_center"],
        }
    ]
    data_sources = cast(dict[str, ModelInput], workspaces["data_sources"])
    data_sources["capabilities"] = [
        {
            "capability_id": "fred",
            "provider": "fred",
            "label": "FRED",
            "state": "empty",
            "entitlement": "research_only",
            "observed_at": _NOW,
            "trace_id": _TRACE_IDS["data_sources"],
        }
    ]
    derivatives = cast(dict[str, ModelInput], workspaces["derivatives"])
    derivatives["workbench"] = cast(
        ModelInput,
        empty_workbench_payload(_NOW, _TRACE_IDS["derivatives"]),
    )
    nodes = [_node(trace_id, "source_receipt") for trace_id in _TRACE_IDS.values()]
    nodes.extend(
        [
            _node("trace-command-terminal", "process_receipt"),
            _node("trace-research-terminal", "reviewer_decision"),
            _node("trace-strategies-terminal", "reviewer_decision"),
            _node("trace-paper-terminal", "paper_receipt"),
            _node("trace-system-terminal", "process_receipt"),
        ]
    )
    return cast(
        dict[str, ModelInput],
        {
            "schema_version": 2,
            "snapshot_id": uuid.UUID("019c0014-f0f5-7000-8000-000000000100"),
            "generated_at": _NOW,
            "source": "local-redacted-projector",
            "workspaces": workspaces,
            "traces": {
                "nodes": nodes,
                "edges": [
                    _edge("trace-command", "trace-command-terminal", "executed_as"),
                    _edge("trace-research", "trace-research-terminal", "reviewed_by"),
                    _edge("trace-strategies", "trace-strategies-terminal", "reviewed_by"),
                    _edge("trace-paper", "trace-paper-terminal", "reconciled_by"),
                    _edge("trace-system", "trace-system-terminal", "executed_as"),
                ],
            },
            "projection": {
                "redaction_policy_version": "dashboard-redaction-v2",
                "reader_versions": ["fixture-v1"],
                "source_schema_version": 2,
                "total_count": 0,
                "projected_count": 0,
                "truncated": False,
            },
        },
    )


def mutate_invalid(payload: dict[str, ModelInput], mutation: str) -> None:
    traces = cast(dict[str, ModelInput], payload["traces"])
    nodes = cast(list[ModelInput], traces["nodes"])
    edges = cast(list[ModelInput], traces["edges"])
    workspaces = cast(dict[str, ModelInput], payload["workspaces"])
    if mutation == "future":
        payload["generated_at"] = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    elif mutation == "loading":
        cast(dict[str, ModelInput], workspaces["system"])["state"] = "loading"
    elif mutation == "duplicate":
        nodes.append(nodes[0])
    elif mutation == "duplicate_edge":
        edges.append(edges[0])
    elif mutation == "dangling_edge":
        edges.append(_edge("trace-overview", "missing-node", "derived_from"))
    elif mutation == "dangling_workspace":
        cast(dict[str, ModelInput], workspaces["system"])["trace_id"] = "missing-node"
    elif mutation == "dangling_agent":
        command = cast(dict[str, ModelInput], workspaces["command_center"])
        agents = cast(list[ModelInput], command["agents"])
        cast(dict[str, ModelInput], agents[0])["trace_id"] = "missing-node"
    elif mutation == "dangling_capability":
        sources = cast(dict[str, ModelInput], workspaces["data_sources"])
        capabilities = cast(list[ModelInput], sources["capabilities"])
        cast(dict[str, ModelInput], capabilities[0])["trace_id"] = "missing-node"
    elif mutation == "no_source":
        for node in nodes:
            candidate = cast(dict[str, ModelInput], node)
            if candidate["kind"] == "source_receipt":
                candidate["kind"] = "observation"
    elif mutation == "no_terminal":
        traces["nodes"] = [node for node in nodes if cast(dict[str, ModelInput], node)["kind"] == "source_receipt"]
        traces["edges"] = []
    elif mutation == "cycle":
        edges.append(_edge("trace-research-terminal", "trace-research", "reviewed_by"))
    elif mutation == "reversed":
        traces["edges"] = [
            {
                **cast(dict[str, ModelInput], edge),
                "from_node_id": cast(dict[str, ModelInput], edge)["to_node_id"],
                "to_node_id": cast(dict[str, ModelInput], edge)["from_node_id"],
            }
            for edge in edges
        ]
    elif mutation == "wrong_domain":
        _find_node(nodes, "trace-research-terminal")["kind"] = "deployment_receipt"
    else:
        raise ValueError(mutation)


def mutate_nested_future(payload: dict[str, ModelInput], target: str) -> None:
    future = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    traces = cast(dict[str, ModelInput], payload["traces"])
    nodes = cast(list[ModelInput], traces["nodes"])
    workspaces = cast(dict[str, ModelInput], payload["workspaces"])
    system = cast(dict[str, ModelInput], workspaces["system"])
    if target == "trace":
        cast(dict[str, ModelInput], nodes[0])["observed_at"] = future
    elif target == "workspace":
        system["observed_at"] = future
    elif target == "freshness":
        cast(dict[str, ModelInput], system["freshness"])["as_of"] = future
    elif target == "item":
        system.update(
            state="populated",
            items=[_workspace_item(future)],
            total_count=1,
            projected_count=1,
        )
    elif target == "capability":
        sources = cast(dict[str, ModelInput], workspaces["data_sources"])
        capabilities = cast(list[ModelInput], sources["capabilities"])
        cast(dict[str, ModelInput], capabilities[0])["observed_at"] = future
    else:
        raise ValueError(target)


def mutate_state_without_observation(
    payload: dict[str, ModelInput],
    state: str,
) -> None:
    workspaces = cast(dict[str, ModelInput], payload["workspaces"])
    overview = cast(dict[str, ModelInput], workspaces["overview"])
    overview["state"] = state
    overview["observed_at"] = None
    overview["blocker_code"] = "fixture_blocked" if state in {"blocked", "error", "corrupt"} else None
    if state == "populated":
        overview.update(
            items=[_workspace_item(_NOW)],
            total_count=1,
            projected_count=1,
        )


def mutate_unavailable_overview(
    payload: dict[str, ModelInput],
    *,
    with_terminal: bool,
) -> None:
    workspaces = cast(dict[str, ModelInput], payload["workspaces"])
    overview = cast(dict[str, ModelInput], workspaces["overview"])
    overview.update(
        state="unavailable",
        observed_at=None,
        blocker_code="authority_absent",
    )
    if not with_terminal:
        return
    traces = cast(dict[str, ModelInput], payload["traces"])
    nodes = cast(list[ModelInput], traces["nodes"])
    edges = cast(list[ModelInput], traces["edges"])
    nodes.append(_node("trace-overview-blocker", "blocker_terminal"))
    edges.append(_edge("trace-overview", "trace-overview-blocker", "blocked_by"))


def _state(trace_id: str) -> dict[str, ModelInput]:
    return {
        "state": "empty",
        "observed_at": _NOW,
        "freshness": {"policy_id": "snapshot-current", "age_seconds": 0, "as_of": _NOW},
        "blocker_code": None,
        "summary": "권위 있는 읽기 완료, 항목 없음",
        "total_count": 0,
        "projected_count": 0,
        "truncated": False,
        "trace_id": trace_id,
        "items": [],
    }


def _workspace_item(observed_at: dt.datetime) -> dict[str, ModelInput]:
    return {
        "item_id": "fixture-item",
        "kind": "metric",
        "label": "fixture",
        "state": "populated",
        "value": "1",
        "observed_at": observed_at,
        "trace_id": _TRACE_IDS["system"],
    }


def _node(node_id: str, kind: str) -> dict[str, ModelInput]:
    return {
        "node_id": node_id,
        "kind": kind,
        "label": "fixture",
        "observed_at": _NOW,
        "safe_ref": "a" * 64,
        "state": "accepted",
        "source_namespace": "dashboard.fixture",
    }


def _edge(from_id: str, to_id: str, kind: str) -> dict[str, ModelInput]:
    return {"from_node_id": from_id, "to_node_id": to_id, "kind": kind}


def _find_node(nodes: list[ModelInput], node_id: str) -> dict[str, ModelInput]:
    for node in nodes:
        candidate = cast(dict[str, ModelInput], node)
        if candidate["node_id"] == node_id:
            return candidate
    raise ValueError(node_id)
