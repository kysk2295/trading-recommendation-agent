from __future__ import annotations

import pytest
from pydantic import ValidationError

import trading_agent.dashboard_models as models
from tests.dashboard_models_v2_fixtures import (
    mutate_invalid,
    mutate_nested_future,
    mutate_state_without_observation,
    mutate_unavailable_overview,
    snapshot_payload,
)


def test_dashboard_snapshot_v2_is_strict_and_bounded() -> None:
    payload = snapshot_payload()

    model = models.DashboardSnapshotV2.model_validate(payload)

    assert model.schema_version == 2
    with pytest.raises(ValidationError):
        models.DashboardSnapshotV2.model_validate({**payload, "api_token": "forbidden"})


def test_dashboard_snapshot_v2_rejects_inconsistent_truncation_metadata() -> None:
    payload = snapshot_payload()
    payload["projection"] = {
        "redaction_policy_version": "dashboard-redaction-v2",
        "reader_versions": ["fixture-v1"],
        "source_schema_version": 2,
        "total_count": 1,
        "projected_count": 0,
        "truncated": False,
    }

    with pytest.raises(ValidationError):
        models.DashboardSnapshotV2.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("future", "generated_at_too_far_future"),
        ("loading", "publisher_loading_state"),
        ("duplicate", "duplicate_trace_node"),
        ("dangling_edge", "dangling_trace_edge"),
        ("dangling_workspace", "dangling_trace_reference"),
        ("dangling_agent", "dangling_trace_reference"),
        ("dangling_capability", "dangling_trace_reference"),
        ("no_source", "trace_source_missing"),
        ("no_terminal", "trace_terminal_missing"),
        ("cycle", "cyclic_trace_graph"),
    ],
)
def test_dashboard_snapshot_v2_enforces_trace_and_publisher_invariants(
    mutation: str,
    reason: str,
) -> None:
    payload = snapshot_payload()
    mutate_invalid(payload, mutation)

    with pytest.raises(ValidationError, match=reason):
        models.DashboardSnapshotV2.model_validate(payload)


def test_dashboard_snapshot_v2_rejects_reversed_terminal_lineage() -> None:
    payload = snapshot_payload()
    mutate_invalid(payload, "reversed")

    with pytest.raises(ValidationError, match="trace_terminal_missing"):
        models.DashboardSnapshotV2.model_validate(payload)


def test_dashboard_snapshot_v2_rejects_wrong_domain_terminal() -> None:
    payload = snapshot_payload()
    mutate_invalid(payload, "wrong_domain")

    with pytest.raises(ValidationError, match="trace_terminal_wrong_domain"):
        models.DashboardSnapshotV2.model_validate(payload)


def test_dashboard_snapshot_v2_rejects_duplicate_directed_edge() -> None:
    payload = snapshot_payload()
    mutate_invalid(payload, "duplicate_edge")

    with pytest.raises(ValidationError, match="duplicate_trace_edge"):
        models.DashboardSnapshotV2.model_validate(payload)


@pytest.mark.parametrize("target", ["trace", "workspace", "freshness", "item", "capability"])
def test_dashboard_snapshot_v2_rejects_nested_future_observations(target: str) -> None:
    payload = snapshot_payload()
    mutate_nested_future(payload, target)

    with pytest.raises(ValidationError, match="observation_too_far_future"):
        models.DashboardSnapshotV2.model_validate(payload)


@pytest.mark.parametrize("state", ["populated", "empty", "stale", "blocked", "error", "corrupt"])
def test_dashboard_snapshot_v2_requires_observation_for_present_authority(state: str) -> None:
    payload = snapshot_payload()
    mutate_state_without_observation(payload, state)

    with pytest.raises(ValidationError, match="observed_at_required"):
        models.DashboardSnapshotV2.model_validate(payload)


def test_dashboard_snapshot_v2_allows_absent_unavailable_authority_with_blocker() -> None:
    payload = snapshot_payload()
    mutate_unavailable_overview(payload, with_terminal=True)

    assert models.DashboardSnapshotV2.model_validate(payload).workspaces.overview.observed_at is None


def test_dashboard_snapshot_v2_rejects_unavailable_authority_without_blocker() -> None:
    payload = snapshot_payload()
    mutate_unavailable_overview(payload, with_terminal=False)

    with pytest.raises(ValidationError, match="trace_terminal_missing"):
        models.DashboardSnapshotV2.model_validate(payload)
