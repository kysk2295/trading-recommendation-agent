from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import run_alpaca_pilot_orb
import run_alpaca_pilot_scanner


@pytest.mark.parametrize(
    "script",
    [
        Path(run_alpaca_pilot_scanner.__file__),
        Path(run_alpaca_pilot_orb.__file__),
    ],
    ids=["scanner", "orb"],
)
def test_alpaca_pilot_cli_builds_with_fixed_window_options(script: Path) -> None:
    # Given/When
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    assert "--start" in completed.stdout
    assert "--end" in completed.stdout
