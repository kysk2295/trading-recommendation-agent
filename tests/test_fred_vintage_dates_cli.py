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
SCRIPT = ROOT / "run_fred_vintage_dates_collect.py"
FIXTURE = Path(__file__).parent / "fixtures/fred_vintage_dates/dff_july.json"


def test_cli_collects_vintage_dates_and_replays_without_credentials(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    registry = tmp_path / "registry.sqlite3"
    output = tmp_path / "output"

    first = _run_cli(state, registry, output, FIXTURE)
    replay = _run_cli(
        state,
        registry,
        output,
        tmp_path / "missing.json",
        credential=tmp_path / "missing.env",
    )

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first.stdout
    assert "artifact_created=no" in replay.stdout
    artifacts = tuple(output.glob("fred_vintage_dates_snapshot_*.json"))
    assert len(artifacts) == 1
    snapshot = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert snapshot["series_id"] == "DFF"
    assert snapshot["vintage_dates"] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    ]
    report = (output / "fred_vintage_dates_ko.md").read_text(encoding="utf-8")
    assert "- result: success" in report
    assert "- vintage date count: 3" in report
    assert "- network access: 0" in report
    capability = DataCapabilityRegistryStore(registry).snapshot(
        as_of=dt.datetime.now(dt.UTC),
        source_ids=(
            DataSourceId(provider="fred", feed="series_vintage_dates"),
        ),
    ).capabilities
    assert len(capability) == 1
    assert capability[0].health_state is DataHealthState.COMPLETE
    for path in (*state.glob("*.json"), *artifacts, output / "fred_vintage_dates_ko.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cli_rejects_invalid_range_before_creating_state(tmp_path: Path) -> None:
    state = tmp_path / "state"
    registry = tmp_path / "registry.sqlite3"
    output = tmp_path / "output"

    completed = _run_cli(
        state,
        registry,
        output,
        FIXTURE,
        realtime_start="2026-07-24",
        realtime_end="2026-07-01",
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
    realtime_start: str = "2026-07-01",
    realtime_end: str = "2026-07-24",
    credential: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--collection-id",
        "fred-dff-vintages-20260724",
        "--series-id",
        "DFF",
        "--realtime-start",
        realtime_start,
        "--realtime-end",
        realtime_end,
        "--limit",
        "100",
        "--state-dir",
        str(state),
        "--capability-registry",
        str(registry),
        "--output-dir",
        str(output),
        "--fixture-response",
        str(fixture),
    ]
    if credential is not None:
        command.extend(("--credential-file", str(credential)))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
