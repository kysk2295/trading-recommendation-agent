from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import run_research_hypothesis_register as registration_cli

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_research_hypothesis_register.py"
EXAMPLE = PROJECT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
REPORT_NAME = "research_hypothesis_registration_ko.md"
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)


def test_research_hypothesis_register_help_is_available() -> None:
    completed = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )

    assert completed.returncode == 0
    assert "--manifest" in completed.stdout
    assert "--database" in completed.stdout
    assert "--output-dir" in completed.stdout


def test_research_hypothesis_register_missing_manifest_is_blocked_without_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.sqlite3"
    output = tmp_path / "report"

    return_code = registration_cli.main(
        (
            "--manifest",
            str(tmp_path / "missing.json"),
            "--database",
            str(database),
            "--output-dir",
            str(output),
        )
    )

    report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert return_code == 1
    assert not database.exists()
    assert "결과: blocked" in report
    assert str(database) not in report
    assert "external mutation: 0" in report


def test_research_hypothesis_register_fixture_replays_with_private_artifacts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.sqlite3"
    output = tmp_path / "report"
    arguments = (
        "--manifest",
        str(EXAMPLE),
        "--database",
        str(database),
        "--output-dir",
        str(output),
    )

    first = registration_cli.main(arguments)
    first_report = (output / REPORT_NAME).read_text(encoding="utf-8")
    replay = registration_cli.main(arguments)
    replay_report = (output / REPORT_NAME).read_text(encoding="utf-8")

    assert first == 0
    assert replay == 0
    assert "research source 신규/재사용: 2/0" in first_report
    assert "hypothesis card 신규/재사용: 1/0" in first_report
    assert "research source 신규/재사용: 0/2" in replay_report
    assert "hypothesis card 신규/재사용: 0/1" in replay_report
    assert "external mutation: 0" in replay_report
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{database}.writer.lock").stat().st_mode) == 0o600
    assert stat.S_IMODE((output / REPORT_NAME).stat().st_mode) == 0o600


def test_registration_path_has_no_provider_or_paper_imports() -> None:
    sources = (
        (PROJECT / "run_research_hypothesis_register.py").read_text(encoding="utf-8"),
        (PROJECT / "trading_agent" / "research_hypothesis_registration.py").read_text(encoding="utf-8"),
    )

    assert all("alpaca" not in source.lower() for source in sources)
    assert all("paper_" not in source.lower() for source in sources)
    assert all("kis_" not in source.lower() for source in sources)


def _execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
