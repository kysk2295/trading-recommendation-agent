from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from tests.dashboard_provider_positive_fixture import (
    NOW,
    RAW_CANARIES,
    build_positive_kr_source,
    build_positive_provider_outputs,
)
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.kr_theme_models import KrCatalystSource

PROVIDERS = ("alfred", "treasury", "opendart", "kis", "ls")
type JsonValue = (
    str
    | int
    | float
    | bool
    | None
    | dict[str, "JsonValue"]
    | list["JsonValue"]
)


def test_five_native_provider_stores_project_exact_positive_authority(
    tmp_path: Path,
) -> None:
    # Given five distinct native provider stores with controlled positive receipts
    outputs = tmp_path / "outputs"
    expected = build_positive_provider_outputs(outputs)

    # When the canonical projector and Python parser consume them
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)
    parsed = DashboardSnapshotV2.model_validate_json(snapshot.model_dump_json())

    # Then each provider exposes exact value, health, entitlement, freshness and SHA
    capabilities = {
        capability.provider: capability
        for capability in parsed.workspaces.data_sources.capabilities
    }
    items = {
        item.item_id.removeprefix("source."): item
        for item in parsed.workspaces.data_sources.items
    }
    traces = {node.node_id: node for node in parsed.traces.nodes}
    for provider in PROVIDERS:
        capability = capabilities[provider]
        item = items[provider]
        assert capability.state == "populated"
        assert capability.entitlement == "research_only"
        assert capability.observed_at == expected[provider].observed_at
        assert item.state == "populated"
        assert item.value == expected[provider].value
        assert item.observed_at == capability.observed_at
        assert traces[capability.trace_id].safe_ref == expected[provider].safe_ref
        assert traces[capability.trace_id].state == "accepted"
    assert parsed.workspaces.data_sources.freshness.policy_id == (
        "provider-specific-authority-v2"
    )
    assert parsed.workspaces.data_sources.freshness.age_seconds == 0
    assert parsed.workspaces.data_sources.freshness.as_of == NOW
    assert all(
        capabilities[provider].state == "unavailable"
        for provider in ("fred", "cftc", "alpaca")
    )

    # And no raw receipt secret or secret-shaped field crosses the recursive boundary
    document = parsed.model_dump(mode="json")
    serialized = json.dumps(document, ensure_ascii=False).lower()
    assert all(canary not in serialized for canary in RAW_CANARIES)
    assert not _contains_forbidden_key(cast("JsonValue", document))


def test_opendart_native_path_cannot_populate_ls_or_kis(tmp_path: Path) -> None:
    # Given only the source-specific OpenDART typed path exists
    outputs = tmp_path / "outputs"
    expected = build_positive_kr_source(
        outputs,
        "opendart",
        KrCatalystSource.DART,
    )

    # When all provider adapters project the same output tree
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then OpenDART is exact while LS and KIS remain independently unavailable
    capabilities = {
        capability.provider: capability
        for capability in snapshot.workspaces.data_sources.capabilities
    }
    assert capabilities["opendart"].state == "populated"
    assert capabilities["ls"].state == "unavailable"
    assert capabilities["kis"].state == "unavailable"
    item = next(
        item
        for item in snapshot.workspaces.data_sources.items
        if item.item_id == "source.opendart"
    )
    trace = next(node for node in snapshot.traces.nodes if node.node_id == item.trace_id)
    assert item.value == expected.value
    assert trace.safe_ref == expected.safe_ref


def _contains_forbidden_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return any(
            _forbidden(str(key)) or _contains_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _forbidden(key: str) -> bool:
    lowered = key.lower()
    return any(
        token in lowered
        for token in ("api_key", "secret", "token", "credential", "authorization")
    )
