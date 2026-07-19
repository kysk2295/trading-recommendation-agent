from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_composite as composite_cli
from tests.test_kr_theme_day_composite import (
    DAY_VERSION,
    OPPORTUNITY_VERSION,
    REGISTERED_AT,
    _component_ledger,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_composite.py"


def test_composite_cli_registers_and_replays_private_report(tmp_path: Path) -> None:
    # Given
    ledger = _component_ledger(tmp_path)
    output = tmp_path / "output"
    argv = _argv(ledger.path, output)

    # When
    first = subprocess.run((str(SCRIPT), *argv), cwd=ROOT, check=False)
    second = subprocess.run((str(SCRIPT), *argv), cwd=ROOT, check=False)

    # Then
    report = output / composite_cli.REPORT_NAME
    assert (first.returncode, second.returncode) == (0, 0)
    assert len(ledger.multi_market_hypotheses()) == 3
    assert "hypothesis created/reused: 0/1" in report.read_text(encoding="utf-8")
    assert "external account/order mutation: 0" in report.read_text(encoding="utf-8")
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


def test_composite_cli_help_has_no_execution_authority_surface() -> None:
    # Given / When
    completed = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)

    # Then
    output = (completed.stdout + completed.stderr).lower()
    assert completed.returncode == 0
    assert "--opportunity-strategy-version" in output
    for forbidden in ("--account", "--arm", "--broker", "--endpoint", "--order"):
        assert forbidden not in output


def _argv(database: Path, output: Path) -> tuple[str, ...]:
    return (
        "--day-strategy-version",
        DAY_VERSION,
        "--opportunity-strategy-version",
        OPPORTUNITY_VERSION,
        "--registered-at",
        REGISTERED_AT.isoformat(),
        "--database",
        str(database),
        "--output-dir",
        str(output),
    )
