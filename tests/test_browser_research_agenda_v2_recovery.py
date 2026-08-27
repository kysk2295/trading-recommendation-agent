from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.test_browser_research_agenda import NOW, agenda_services_fixture
from trading_agent.browser_research_agenda import ContinuousBrowserResearchSupervisor
from trading_agent.browser_research_agenda_contract import create_episode, evidence_for_episode


def test_v2_reconstructs_missing_v1_task_before_one_concurrent_successor(tmp_path: Path) -> None:
    # Given: a committed v1 episode whose matching task write was interrupted.
    services = agenda_services_fixture(tmp_path)
    legacy_episode = create_episode(None, NOW, 1)
    assert services.cycles.append_evidence(evidence_for_episode(legacy_episode))
    migrated = ContinuousBrowserResearchSupervisor(
        services.supervisor, services.cycles, owns_cycles=False, agenda_version=2
    )

    # When: two startup paths recover and migrate the agenda concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        successors = tuple(pool.map(migrated.ensure_open, (NOW, NOW)))

    # Then: the exact v1 task exists and owns the single v2 successor lineage.
    predecessor = services.supervisor.runtime.tasks.reader().task(legacy_episode.task_id)
    assert predecessor is not None
    assert successors[0].task_id == successors[1].task_id != predecessor.task_id
    episodes = migrated.episodes.all()
    assert len(episodes) == 2
    assert episodes[-1].predecessor_task_id == predecessor.task_id

    # When/Then: another startup replay creates neither another task nor episode.
    replay = migrated.ensure_open(NOW + dt.timedelta(seconds=1))
    assert replay.task_id == successors[0].task_id
    assert len(migrated.episodes.all()) == 2
    assert len(services.supervisor.runtime.tasks.reader().tasks()) == 2
