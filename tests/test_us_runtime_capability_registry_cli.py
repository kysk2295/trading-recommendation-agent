from __future__ import annotations

import stat
from pathlib import Path

import pytest

import run_us_runtime_capability_registry as cli
from tests.alpaca_sip_runtime_fleet_fixtures import decision, feature_requests
from tests.test_us_market_data_fleet_audit import _cycle
from trading_agent.us_market_data_fleet_audit import build_runtime_fleet_audit
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore


def test_help_exposes_local_audit_projection_only() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    assert raised.value.code == 0


def test_ready_audit_appends_registry_once(tmp_path: Path) -> None:
    audit_path = _audit(tmp_path, gap_symbol=None)
    registry = tmp_path / "registry.sqlite3"
    output = tmp_path / "report"
    arguments = _arguments(audit_path, registry, output)

    first = cli.main(arguments)
    first_report = (output / cli.REPORT_NAME).read_text()
    second = cli.main(arguments)
    report_path = output / cli.REPORT_NAME
    second_report = report_path.read_text()

    assert first == second == 0
    assert "result: complete" in first_report
    assert "capability appended: 1" in first_report
    assert "entitlement appended: 1" in first_report
    assert "capability appended: 0" in second_report
    assert "entitlement appended: 0" in second_report
    assert "capability resolved: 1/1" in second_report
    assert "entitlement resolved: 1/1" in second_report
    assert "owner ready: 2/2" in second_report
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_degraded_audit_is_incomplete_and_path_collision_blocks(tmp_path: Path) -> None:
    audit_path = _audit(tmp_path, gap_symbol="BBB")
    output = tmp_path / "report"

    incomplete = cli.main(_arguments(audit_path, tmp_path / "registry.sqlite3", output))
    collision = cli.main(_arguments(audit_path, audit_path, tmp_path / "collision"))

    assert incomplete == 2
    assert "result: incomplete" in (output / cli.REPORT_NAME).read_text()
    assert collision == 1


def _audit(tmp_path: Path, *, gap_symbol: str | None) -> Path:
    result, gate = _cycle(tmp_path, gap_symbol=gap_symbol)
    path = tmp_path / "audit.sqlite3"
    record = build_runtime_fleet_audit(decision(), feature_requests(), result, gate)
    assert RuntimeFleetAuditStore(path).append(record)
    return path


def _arguments(audit: Path, registry: Path, output: Path) -> tuple[str, ...]:
    return (
        "--audit-store",
        str(audit),
        "--registry",
        str(registry),
        "--output-dir",
        str(output),
    )
