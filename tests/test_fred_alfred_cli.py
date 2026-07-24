from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from trading_agent.data_capability_models import DataHealthState, DataSourceId
from trading_agent.data_capability_registry import DataCapabilityRegistryStore
from trading_agent.fred_alfred_config import (
    FredCredentialFileError,
    load_fred_credentials,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_fred_alfred_collect.py"
FIXTURES = Path(__file__).parent / "fixtures/fred_alfred"


def test_cli_collects_fred_and_alfred_then_replays_without_credentials(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "capability/registry.sqlite3"
    cases = (
        ("fred", FIXTURES / "fred_cpi_three.json", None, 3),
        ("alfred", FIXTURES / "alfred_cpi_vintage_two.json", "2024-04-01", 2),
    )
    for mode, fixture, vintage, expected_count in cases:
        state = tmp_path / f"{mode}-state"
        output = tmp_path / f"{mode}-output"
        first = _run_cli(mode, state, registry, output, fixture, vintage)
        replay = _run_cli(
            mode,
            state,
            registry,
            output,
            tmp_path / "missing.json",
            vintage,
            credential_file=tmp_path / "missing.env",
        )

        assert first.returncode == 0, first.stderr
        assert replay.returncode == 0, replay.stderr
        assert "artifact_created=yes" in first.stdout
        assert "artifact_created=no" in replay.stdout
        artifacts = tuple(output.glob("fred_alfred_snapshot_*.json"))
        assert len(artifacts) == 1
        snapshot = json.loads(artifacts[0].read_text(encoding="utf-8"))
        assert snapshot["source_mode"] == mode
        assert len(snapshot["observations"]) == expected_count
        report = (output / "fred_alfred_ko.md").read_text(encoding="utf-8")
        assert "- result: success" in report
        assert f"- observation count: {expected_count}" in report
        assert "- network access: 0" in report
        assert "- provider operation: stored receipt query-only" in report
        assert "- broker, account, order, or allocation mutation: none" in report
        for path in (*state.glob("*.json"), artifacts[0], output / "fred_alfred_ko.md"):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    snapshot = DataCapabilityRegistryStore(registry).snapshot(
        as_of=dt.datetime.now(dt.UTC),
        source_ids=(
            DataSourceId(provider="alfred", feed="vintage_observations"),
            DataSourceId(provider="fred", feed="series_observations"),
        ),
    )
    assert [capability.health_state for capability in snapshot.capabilities] == [
        DataHealthState.COMPLETE,
        DataHealthState.COMPLETE,
    ]
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600


def test_cli_rejects_alfred_without_vintage_before_creating_state(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    registry = tmp_path / "registry.sqlite3"
    output = tmp_path / "output"

    completed = _run_cli(
        "alfred",
        state,
        registry,
        output,
        FIXTURES / "alfred_cpi_vintage_two.json",
        None,
    )

    assert completed.returncode == 2
    assert not state.exists()
    assert not registry.exists()
    assert not output.exists()


def test_fred_credentials_require_current_owner_mode_600_regular_file(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "fred.env"
    secret.write_text(f"FRED_API_KEY={'a' * 32}\n", encoding="utf-8")
    secret.chmod(0o600)

    credentials = load_fred_credentials(secret)

    assert repr(credentials) == "FredCredentials()"
    secret.chmod(0o644)
    with pytest.raises(FredCredentialFileError):
        _ = load_fred_credentials(secret)


def _run_cli(
    mode: str,
    state: Path,
    registry: Path,
    output: Path,
    fixture: Path,
    vintage: str | None,
    *,
    credential_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--mode",
        mode,
        "--collection-id",
        f"{mode}-cpi-20260724",
        "--series-id",
        "CPIAUCSL",
        "--observation-start",
        "2024-01-01",
        "--observation-end",
        "2024-03-01",
        "--limit",
        "10",
        "--state-dir",
        str(state),
        "--capability-registry",
        str(registry),
        "--output-dir",
        str(output),
        "--fixture-response",
        str(fixture),
    ]
    if vintage is not None:
        command.extend(("--vintage-date", vintage))
    if credential_file is not None:
        command.extend(("--credential-file", str(credential_file)))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
