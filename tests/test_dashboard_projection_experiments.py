from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.dashboard_projection_experiment_fixture import append_reviewer_and_lifecycle, complete_experiment_outputs
from tests.test_experiment_ledger_store import _research_card, _research_source
from trading_agent.dashboard_models_v2 import TraceEdgeV2
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_projection_experiment_authority import strict_stages
from trading_agent.dashboard_projection_experiment_chains import ExperimentChain
from trading_agent.dashboard_projection_experiments import _projection, project_research, project_strategies
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.lane_policy_models import LaneId as ReviewLaneId

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_research_projection_preserves_ledger_causal_chain_and_blocks_missing_reviewer(
    tmp_path: Path,
) -> None:
    outputs = complete_experiment_outputs(tmp_path)

    projection = project_research(outputs, now=NOW)

    item = projection.workspace.items[0]
    nodes = {node.node_id: node for node in projection.nodes}
    path = _descendants(item.trace_id, projection.edges)
    assert item.state == "blocked"
    assert item.value == f"{_research_card().hypothesis.hypothesis_id} · code:{'a' * 40}"
    assert nodes[f"{item.trace_id}.dataset"].safe_ref == "b" * 64
    assert tuple(nodes[node_id].kind for node_id in path) == (
        "source_receipt",
        "hypothesis",
        "dataset",
        "code_revision",
        "trial",
        "trial",
        "blocker_terminal",
    )
    assert not any(node.kind == "reviewer_decision" for node in projection.nodes)
    assert projection.workspace.blocker_code == "reviewer_missing"


def test_strategy_projection_never_substitutes_lifecycle_for_reviewer(
    tmp_path: Path,
) -> None:
    outputs = complete_experiment_outputs(tmp_path)

    projection = project_strategies(outputs, now=NOW)

    item = projection.workspace.items[0]
    path = _descendants(item.trace_id, projection.edges)
    assert item.state == "blocked"
    nodes = {node.node_id: node for node in projection.nodes}
    assert tuple(nodes[node_id].kind for node_id in path).count("lifecycle_decision") == 0
    assert nodes[path[-1]].kind == "blocker_terminal"
    assert projection.workspace.blocker_code == "reviewer_missing"


def test_strategy_projection_uses_matching_persisted_reviewer_and_lifecycle_terminals(
    tmp_path: Path,
) -> None:
    outputs = complete_experiment_outputs(tmp_path)
    append_reviewer_and_lifecycle(outputs)

    projection = project_strategies(outputs, now=NOW)

    item = projection.workspace.items[0]
    nodes = {node.node_id: node for node in projection.nodes}
    path = _descendants(item.trace_id, projection.edges)
    assert projection.workspace.state == "populated"
    assert item.state == "populated"
    assert tuple(nodes[node_id].kind for node_id in path)[-2:] == (
        "reviewer_decision",
        "lifecycle_decision",
    )
    research = project_research(outputs, now=NOW)
    root_edge = next(edge for edge in research.edges if edge.from_node_id == research.workspace.trace_id)
    research_nodes = {node.node_id: node for node in research.nodes}
    assert root_edge.kind == "reviewed_by"
    assert research_nodes[root_edge.to_node_id].kind == "reviewer_decision"


def test_strategy_projection_rejects_reviewer_linked_to_a_different_trial_terminal(tmp_path: Path) -> None:
    outputs = complete_experiment_outputs(tmp_path)
    append_reviewer_and_lifecycle(outputs, snapshot_key="0" * 64)

    projection = project_strategies(outputs, now=NOW)

    assert projection.workspace.state == "blocked"
    assert projection.workspace.blocker_code == "reviewer_missing"


def test_strategy_projection_rejects_reviewer_from_a_different_lane(tmp_path: Path) -> None:
    outputs = complete_experiment_outputs(tmp_path)
    append_reviewer_and_lifecycle(outputs, review_lane=ReviewLaneId("swing_momentum"))

    projection = project_strategies(outputs, now=NOW)

    assert projection.workspace.blocker_code == "reviewer_missing"


def test_projection_rejects_equal_consecutive_authority_timestamps(tmp_path: Path) -> None:
    outputs = complete_experiment_outputs(tmp_path, strict_stage_times=False)

    assert project_research(outputs, now=NOW).workspace.blocker_code == "timestamp_order_invalid"


def test_projection_rejects_authority_stage_after_observation_time(tmp_path: Path) -> None:
    outputs = complete_experiment_outputs(tmp_path)

    projection = project_research(outputs, now=dt.datetime(2026, 7, 16, 20, 17, tzinfo=dt.UTC))

    assert projection.workspace.blocker_code == "research_future_observation"


def test_strict_stages_rejects_every_authority_stage_after_observation_time() -> None:
    keys = (
        "source_at",
        "hypothesis_at",
        "code_at",
        "trial_at",
        "trial_started_at",
        "terminal_at",
        "reviewed_at",
        "lifecycle_at",
    )
    stages = {
        key: dt.datetime(2026, 7, 20, index, tzinfo=dt.UTC)
        for index, key in enumerate(keys)
    }

    for key in keys:
        assert not strict_stages(stages | {key: NOW + dt.timedelta(seconds=1)}, NOW)


def test_projection_blocks_every_workspace_when_one_candidate_is_incomplete() -> None:
    complete = _chain(blocker=None, reviewer_ref="d" * 64, lifecycle_ref="e" * 64)
    incomplete = _chain(blocker="reviewer_missing", reviewer_ref=None, lifecycle_ref=None)

    projection = _projection("strategies", (complete, incomplete), False, NOW)

    root_edge = next(edge for edge in projection.edges if edge.from_node_id == projection.workspace.trace_id)
    nodes = {node.node_id: node for node in projection.nodes}
    assert projection.workspace.state == "blocked"
    assert projection.workspace.blocker_code == "reviewer_missing"
    assert root_edge.kind == "blocked_by"
    assert nodes[root_edge.to_node_id].label == "reviewer_missing"


def test_projection_blocks_invalid_code_version_before_trial() -> None:
    chain = _chain(code_version="build-v1", blocker="code_sha_invalid")

    projection = _projection("research", (chain,), False, NOW)

    assert projection.workspace.state == "blocked"
    assert projection.workspace.blocker_code == "code_sha_invalid"
    assert projection.workspace.items[0].value == "hypothesis-1 · code:build-v1"


def test_projection_does_not_drop_a_persisted_source_without_a_hypothesis_card(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    ledger = ExperimentLedgerStore(outputs / "experiment_control" / "experiment_ledger.sqlite3")
    with ledger.writer() as writer:
        assert writer.register_research_source(_research_source())

    projection = project_research(outputs, now=NOW)

    item = projection.workspace.items[0]
    nodes = {node.node_id: node for node in projection.nodes}
    path = _descendants(item.trace_id, projection.edges)
    assert item.state == "blocked"
    assert projection.workspace.blocker_code == "source_card_missing"
    assert tuple(nodes[node_id].kind for node_id in path) == (
        "source_receipt",
        "blocker_terminal",
    )


def test_allocation_authority_item_requires_two_independent_champions() -> None:
    chain = _chain(blocker=None, reviewer_ref="d" * 64, lifecycle_ref="e" * 64)

    locked_without_champion = _projection("strategies", (chain,), False, NOW)
    locked_with_one_champion = _projection("strategies", (chain,), False, NOW)
    available_with_two_champions = _projection("strategies", (chain,), True, NOW)

    assert _allocation_item(locked_without_champion).state == "blocked"
    assert _allocation_item(locked_with_one_champion).state == "blocked"
    assert _allocation_item(available_with_two_champions).state == "populated"
    assert _allocation_item(available_with_two_champions).value == "Authority present · read-only; no mutation control"


def _chain(
    *,
    code_version: str = "a" * 40,
    blocker: str | None,
    reviewer_ref: str | None = None,
    lifecycle_ref: str | None = None,
) -> ExperimentChain:
    return ExperimentChain(
        source_ref="a" * 64,
        hypothesis_ref="b" * 64,
        dataset_sha="c" * 64,
        code_ref="d" * 64 if code_version == "a" * 40 else None,
        code_version=code_version,
        trial_ref="e" * 64,
        terminal_ref="f" * 64,
        reviewer_ref=reviewer_ref,
        lifecycle_ref=lifecycle_ref,
        label="lane-1",
        value="hypothesis-1",
        observed_at=NOW,
        blocker=blocker,
    )


def _allocation_item(projection: WorkspaceProjection):
    return next(item for item in projection.workspace.items if item.item_id == "strategies.allocation_authority")


def _descendants(
    root_id: str,
    edges: tuple[TraceEdgeV2, ...],
) -> tuple[str, ...]:
    ordered = [root_id]
    seen = {root_id}
    for node_id in ordered:
        for edge in edges:
            if edge.from_node_id == node_id and edge.to_node_id not in seen:
                seen.add(edge.to_node_id)
                ordered.append(edge.to_node_id)
    return tuple(ordered)
