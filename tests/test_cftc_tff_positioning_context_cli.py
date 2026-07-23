from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
from pathlib import Path

from tests.test_cftc_tff_parser import FIXTURE
from trading_agent.cftc_tff_models import CftcTffPositioningContext

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_cftc_tff_positioning_context.py"


def test_fixture_cli_publishes_context_and_replays_without_network(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "state/cftc-tff.sqlite3"
    output = tmp_path / "output"

    # When
    first = _run_cli(database, output, FIXTURE)
    artifacts = tuple(output.glob("cftc_tff_context_*.json"))
    first_sha = hashlib.sha256(artifacts[0].read_bytes()).hexdigest()
    replay = _run_cli(
        database,
        output,
        tmp_path / "nonexistent.json",
    )

    # Then
    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=yes" in first.stdout
    assert "artifact_created=no" in replay.stdout
    assert len(artifacts) == 1
    assert hashlib.sha256(artifacts[0].read_bytes()).hexdigest() == first_sha
    context = CftcTffPositioningContext.model_validate_json(artifacts[0].read_bytes())
    assert context.contract_market_code == "13874A"
    assert len(context.categories) == 5
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    report_path = output / "cftc_tff_positioning_context_ko.md"
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    report = report_path.read_text(encoding="utf-8")
    assert "- report count: 2" in report
    assert "- category count: 5" in report
    assert "- network access: 0" in report
    assert "- broker, account, order, or allocation mutation: none" in report
    assert "1941500" not in report
    assert str(tmp_path) not in report


def test_invalid_contract_market_code_creates_no_state(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "state/cftc-tff.sqlite3"
    output = tmp_path / "output"

    # When
    completed = _run_cli(
        database,
        output,
        FIXTURE,
        contract_market_code="bad",
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
    contract_market_code: str = "13874A",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--collection-id",
            "es-tff-20260724",
            "--contract-market-code",
            contract_market_code,
            "--through-date",
            "2026-07-24",
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
