from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

from trading_agent.data_capability_models import DataHealthState, DataSourceId
from trading_agent.data_capability_registry import DataCapabilityRegistryStore

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_arxiv_research_collect.py"
FIXTURE = Path(__file__).parent / "fixtures/arxiv_research/query_two_papers.xml"
SOURCE = DataSourceId(provider="arxiv", feed="api_query")


def test_arxiv_research_cli_collects_replays_and_registers_capability(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    registry = tmp_path / "capability/registry.sqlite3"
    output = tmp_path / "output"

    first = _run_cli(state, registry, output, FIXTURE)
    replay = _run_cli(state, registry, output, tmp_path / "missing.xml")

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first.stdout
    assert "artifact_created=no" in replay.stdout
    artifacts = tuple(output.glob("arxiv_research_snapshot_*.json"))
    assert len(artifacts) == 1
    snapshot = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert snapshot["total_results"] == 2
    assert [paper["arxiv_id"] for paper in snapshot["papers"]] == [
        "2312.00002v1",
        "2401.00001v2",
    ]
    capability = DataCapabilityRegistryStore(registry).snapshot(
        as_of=dt.datetime.now(dt.UTC),
        source_ids=(SOURCE,),
    ).capabilities[0]
    assert capability.health_state is DataHealthState.COMPLETE
    report = (output / "arxiv_research_ko.md").read_text(encoding="utf-8")
    assert "- result: success" in report
    assert "- paper count: 2" in report
    assert "- network access: 0" in report
    assert "- provider operation: stored receipt query-only" in report
    assert "- hypothesis, strategy, trial, recommendation, or order mutation: none" in report
    for path in (
        *state.glob("*.json"),
        registry,
        artifacts[0],
        output / "arxiv_research_ko.md",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_arxiv_research_cli_rejects_bad_category_before_state_creation(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    registry = tmp_path / "capability/registry.sqlite3"
    output = tmp_path / "output"

    completed = _run_cli(
        state,
        registry,
        output,
        FIXTURE,
        category="Q-FIN.TR",
    )

    assert completed.returncode == 2
    assert not state.exists()
    assert not registry.exists()
    assert not output.exists()


def _run_cli(
    state: Path,
    registry: Path,
    output: Path,
    fixture: Path,
    *,
    category: str = "q-fin.TR",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--collection-id",
            "arxiv-qfin-20260724",
            "--category",
            category,
            "--term",
            "market microstructure",
            "--max-results",
            "2",
            "--state-dir",
            str(state),
            "--capability-registry",
            str(registry),
            "--output-dir",
            str(output),
            "--fixture-response",
            str(fixture),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
