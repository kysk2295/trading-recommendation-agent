from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_kr_same_cycle_source_readiness.py"
REPORT = "kr_same_cycle_source_readiness_ko.md"


def test_cli_identifies_only_the_unavailable_source_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    # Given
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    _ = _secret(
        secrets / "ls.env",
        "LS_APP_KEY=" + "l" * 20 + "\nLS_APP_SECRET=" + "s" * 20 + "\n",
    )
    _ = _secret(
        secrets / "kis.env",
        "KIS_LIVE_APP_KEY=" + "k" * 20 + "\nKIS_LIVE_APP_SECRET=" + "q" * 20 + "\n",
    )
    output = tmp_path / "output"

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--secrets-root",
            str(secrets),
            "--output-dir",
            str(output),
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 1
    report = (output / REPORT).read_text(encoding="utf-8")
    assert "- result: blocked" in report
    assert "- opendart: unavailable" in report
    assert "- ls_nws: ready" in report
    assert "- kis_live: ready" in report
    assert "- network requests: 0" in report
    assert str(tmp_path) not in report
    assert "l" * 20 not in report
    assert "s" * 20 not in report
    assert "k" * 20 not in report
    assert "q" * 20 not in report
    assert stat.S_IMODE((output / REPORT).stat().st_mode) == 0o600


def _secret(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path
