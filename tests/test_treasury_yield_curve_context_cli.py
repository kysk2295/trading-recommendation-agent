from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
from pathlib import Path

from tests.test_treasury_yield_parser import FIXTURE
from trading_agent.treasury_yield_models import TreasuryYieldContext

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_treasury_yield_curve_context.py"


def test_fixture_cli_publishes_context_and_replays_without_network(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "state/treasury-yield.sqlite3"
    output = tmp_path / "output"

    # When
    first = _run_cli(database, output, FIXTURE)
    artifacts = tuple(output.glob("treasury_yield_curve_context_*.json"))
    first_sha = hashlib.sha256(artifacts[0].read_bytes()).hexdigest()
    replay = _run_cli(
        database,
        output,
        tmp_path / "nonexistent.xml",
    )

    # Then
    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first.stdout
    assert "artifact_created=no" in replay.stdout
    assert len(artifacts) == 1
    assert hashlib.sha256(artifacts[0].read_bytes()).hexdigest() == first_sha
    context = TreasuryYieldContext.model_validate_json(
        artifacts[0].read_bytes(),
    )
    assert len(context.points) == 14
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    report_path = output / "treasury_yield_curve_context_ko.md"
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    report = report_path.read_text(encoding="utf-8")
    assert "- curve count: 2" in report
    assert "- maturity count: 14" in report
    assert "- network access: 0" in report
    assert "- broker, account, order, or allocation mutation: none" in report
    assert "4.08" not in report
    assert str(tmp_path) not in report


def test_invalid_through_date_creates_no_state(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "state/treasury-yield.sqlite3"
    output = tmp_path / "output"

    # When
    completed = _run_cli(
        database,
        output,
        FIXTURE,
        through_date="bad",
    )

    # Then
    assert completed.returncode == 2
    assert not database.exists()
    assert not output.exists()


def _run_cli(
    database: Path,
    output: Path,
    fixture: Path,
    *,
    through_date: str = "2026-07-24",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--collection-id",
            "treasury-yield-20260724",
            "--through-date",
            through_date,
            "--database",
            str(database),
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
