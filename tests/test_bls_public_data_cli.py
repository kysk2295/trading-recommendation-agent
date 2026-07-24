from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

from trading_agent.data_capability_models import (
    DataHealthState,
    DataSourceId,
)
from trading_agent.data_capability_registry import DataCapabilityRegistryStore

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_bls_public_data_collect.py"
FIXTURE = Path(__file__).parent / "fixtures/bls_public_data/macro_two_series.json"
SOURCE = DataSourceId(provider="bls", feed="public_data_v1")


def test_bls_public_data_cli_collects_replays_and_registers_capability(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "state/bls.sqlite3"
    registry = tmp_path / "capability/registry.sqlite3"
    output = tmp_path / "output"

    # When
    first = _run_cli(database, registry, output, FIXTURE)
    replay = _run_cli(
        database,
        registry,
        output,
        tmp_path / "missing.json",
    )

    # Then
    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first.stdout
    assert "artifact_created=no" in replay.stdout
    artifacts = tuple(output.glob("bls_macro_snapshot_*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert [item["series_id"] for item in payload["series"]] == [
        "CUUR0000SA0",
        "LNS14000000",
    ]
    assert sum(len(item["observations"]) for item in payload["series"]) == 4
    capability = DataCapabilityRegistryStore(registry).snapshot(
        as_of=dt.datetime.now(dt.UTC),
        source_ids=(SOURCE,),
    ).capabilities[0]
    assert capability.health_state is DataHealthState.COMPLETE
    report = (output / "bls_public_data_ko.md").read_text(encoding="utf-8")
    assert "- result: success" in report
    assert "- series count: 2" in report
    assert "- observation count: 4" in report
    assert "- available observation count: 4" in report
    assert "- missing observation count: 0" in report
    assert "- observed completeness bps: 10000" in report
    assert "- network access: 0" in report
    assert "- provider operation: stored receipt query-only" in report
    assert "- capability health: complete" in report
    assert "- broker, account, order, or allocation mutation: none" in report
    for path in (database, registry, artifacts[0], output / "bls_public_data_ko.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bls_public_data_cli_rejects_bad_series_before_state_creation(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "state/bls.sqlite3"
    registry = tmp_path / "capability/registry.sqlite3"
    output = tmp_path / "output"

    # When
    completed = _run_cli(
        database,
        registry,
        output,
        FIXTURE,
        series_ids=("bad-series",),
    )

    # Then
    assert completed.returncode == 2
    assert not database.exists()
    assert not registry.exists()
    assert not output.exists()


def test_bls_public_data_cli_marks_footnoted_missing_data_degraded(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state/bls.sqlite3"
    registry = tmp_path / "capability/registry.sqlite3"
    output = tmp_path / "output"
    fixture = tmp_path / "missing.json"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["Results"]["series"][0]["data"][0]["value"] = "-"
    payload["Results"]["series"][0]["data"][0]["footnotes"] = [
        {
            "code": "X",
            "text": "Data unavailable due to the 2025 lapse in appropriations.",
        }
    ]
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run_cli(database, registry, output, fixture)

    assert completed.returncode == 0, completed.stderr
    capability = DataCapabilityRegistryStore(registry).snapshot(
        as_of=dt.datetime.now(dt.UTC),
        source_ids=(SOURCE,),
    ).capabilities[0]
    assert capability.health_state is DataHealthState.DEGRADED
    assert capability.observed_completeness_bps == 7_500
    report = (output / "bls_public_data_ko.md").read_text(encoding="utf-8")
    assert "- missing observation count: 1" in report
    assert "- capability health: degraded" in report


def _run_cli(
    database: Path,
    registry: Path,
    output: Path,
    fixture: Path,
    *,
    series_ids: tuple[str, ...] = ("CUUR0000SA0", "LNS14000000"),
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--collection-id",
        "bls-macro-20260724",
    ]
    for series_id in series_ids:
        command.extend(("--series-id", series_id))
    command.extend(
        (
            "--start-year",
            "2025",
            "--end-year",
            "2026",
            "--database",
            str(database),
            "--capability-registry",
            str(registry),
            "--output-dir",
            str(output),
            "--fixture-response",
            str(fixture),
        )
    )
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
