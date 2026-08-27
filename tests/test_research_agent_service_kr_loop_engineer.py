from __future__ import annotations

import datetime as dt
from pathlib import Path

import trading_agent.research_agent_service_projection as projection
from tests.test_kr_loop_engineer_cli import _bundle, _memory_record
from tests.test_research_agent_service_kr_autonomous_runtime import _v4_config
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_operator_paths import kr_autonomous_operator_paths
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore

NOW = dt.datetime(2026, 8, 27, 9, 0, tzinfo=dt.UTC)


def test_service_projection_syncs_new_loop_bundle_without_starting_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Given: schema-v4 service paths with one new outcome-learning bundle.
    config = _v4_config(tmp_path)
    paths = kr_autonomous_operator_paths(config)
    assert paths is not None
    with AutonomousMemoryStore(paths.memory_database).writer() as writer:
        assert writer.append(_memory_record(_bundle()))
    monkeypatch.setattr(projection, "current_main_commit", lambda repository: "a" * 40)

    # When: the service projection synchronization boundary replays twice.
    first = projection.sync_service_kr_loop_bundles(config, paths, NOW)
    replay = projection.sync_service_kr_loop_bundles(config, paths, NOW)

    # Then: one detected candidate exists and no release or coding task directory is created.
    assert first == 1
    assert replay == 0
    snapshots = KrLoopEngineerStore(paths.loop_database).snapshots()
    assert tuple(item.state for item in snapshots) == (KrLoopCandidateState.DETECTED,)
    assert KrLoopEngineerStore(paths.loop_database).releases() == ()
    assert not paths.loop_task_root.exists()
