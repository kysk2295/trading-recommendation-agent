from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
from decimal import Decimal
from pathlib import Path

from tests.trade_update_ledger_fixtures import FINGERPRINT, initialized_store
from trading_agent.lane_contract_models import LaneManifest
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    LANE_CONTRACT_REGISTERED_AT,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_lane_control_plane_bootstrap.py"
UV = shutil.which("uv")
assert UV is not None
UV_PATH = Path(UV)


def test_lane_bootstrap_help_is_available() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0
    assert "--database" in completed.stdout
    assert "--intraday-execution-database" in completed.stdout


def test_registry_only_bootstrap_registers_default_contracts(tmp_path: Path) -> None:
    registry = tmp_path / "lane-registry.sqlite3"
    output = tmp_path / "report"

    completed = _run(registry, output)

    assert completed.returncode == 0, completed.stderr
    store = LaneRegistryStore(registry)
    assert len(store.manifests()) == 3
    assert len(store.experiment_scopes()) == 4
    assert store.account_bindings() == ()
    report = _report(output)
    assert "manifest 신규/전체: 3/3" in report
    assert "experiment scope 신규/전체: 4/4" in report
    assert "intraday account binding: not_requested" in report
    assert "외부 Alpaca mutation: 0건" in report
    assert stat.S_IMODE((output / "lane_control_plane_bootstrap_ko.md").stat().st_mode) == 0o600


def test_registry_bootstrap_replay_is_idempotent(tmp_path: Path) -> None:
    registry = tmp_path / "lane-registry.sqlite3"
    output = tmp_path / "report"
    assert _run(registry, output).returncode == 0

    replay = _run(registry, output)

    assert replay.returncode == 0
    report = _report(output)
    assert "manifest 신규/전체: 0/3" in report
    assert "experiment scope 신규/전체: 0/4" in report


def test_bootstrap_rejects_an_invalid_execution_ledger_before_registry_write(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "lane-registry.sqlite3"
    output = tmp_path / "report"
    missing = tmp_path / "missing-execution.sqlite3"

    completed = _run(registry, output, missing)

    assert completed.returncode == 1
    assert not registry.exists()
    report = _report(output)
    assert "결과: blocked" in report
    assert str(missing) not in report
    assert "외부 Alpaca mutation: 0건" in report


def test_bootstrap_binds_existing_intraday_ledger_without_identifier_output(
    tmp_path: Path,
) -> None:
    execution = initialized_store(tmp_path / "execution")
    registry = tmp_path / "lane-registry.sqlite3"
    output = tmp_path / "report"

    first = _run(registry, output, execution.path)
    replay = _run(registry, output, execution.path)

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    bindings = LaneRegistryStore(registry).account_bindings()
    assert len(bindings) == 1
    assert bindings[0].binding.account_fingerprint == FINGERPRINT
    report = _report(output)
    assert "intraday account binding: already_registered" in report
    assert FINGERPRINT not in report
    assert str(execution.path) not in report
    assert "manifest_key" not in report
    assert "binding_key" not in report


def test_bootstrap_appends_revised_manifests_after_the_base_checkpoint(
    tmp_path: Path,
) -> None:
    execution = initialized_store(tmp_path / "execution")
    registry = tmp_path / "lane-registry.sqlite3"
    output = tmp_path / "report"
    store = LaneRegistryStore(registry)
    with store.writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            _ = writer.register_manifest(_base_checkpoint_manifest(manifest))
        for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
            _ = writer.register_experiment_scope(scope)

    completed = _run(registry, output, execution.path)

    assert completed.returncode == 0, completed.stderr
    versions = {
        stored.manifest.manifest_version
        for stored in store.manifests()
        if stored.manifest.lane_id is LaneId.INTRADAY_MOMENTUM
    }
    assert versions == {"1.0.0", "1.0.1"}
    assert len(store.account_bindings()) == 1
    report = _report(output)
    assert "manifest 신규/전체: 2/3" in report
    assert FINGERPRINT not in report
    assert str(execution.path) not in report


def test_bootstrap_redacts_a_current_version_ledger_with_invalid_schema(
    tmp_path: Path,
) -> None:
    execution = tmp_path / "sensitive-execution-path.sqlite3"
    with sqlite3.connect(execution) as connection:
        _ = connection.execute("PRAGMA user_version = 9")
    registry = tmp_path / "lane-registry.sqlite3"
    output = tmp_path / "report"

    completed = _run(registry, output, execution)

    assert completed.returncode == 1
    assert str(execution) not in completed.stdout
    assert str(execution) not in completed.stderr
    assert not registry.exists()
    report = _report(output)
    assert "결과: blocked" in report
    assert str(execution) not in report
    assert "외부 Alpaca mutation: 0건" in report


def _run(
    registry: Path,
    output: Path,
    execution: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        str(SCRIPT),
        "--database",
        str(registry),
        "--output-dir",
        str(output),
    ]
    if execution is not None:
        command.extend(("--intraday-execution-database", str(execution)))
    return subprocess.run(
        command,
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )


def _report(output: Path) -> str:
    return (output / "lane_control_plane_bootstrap_ko.md").read_text(encoding="utf-8")


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV_PATH.parent}:/usr/bin:/bin"
    return environment


def _base_checkpoint_manifest(manifest: LaneManifest) -> LaneManifest:
    if manifest.lane_id is LaneId.MARKET_REGIME:
        return manifest.model_copy(update={"manifest_version": "1.0.0"})
    legacy_risk = manifest.risk_contract.model_copy(update={"risk_fraction": Decimal("0.0025")})
    return LaneManifest.model_validate(
        {
            **manifest.model_dump(),
            "manifest_version": "1.0.0",
            "registered_at": LANE_CONTRACT_REGISTERED_AT,
            "risk_contract": legacy_risk,
        }
    )
