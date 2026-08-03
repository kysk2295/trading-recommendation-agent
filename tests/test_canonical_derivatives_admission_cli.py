from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_canonical_derivatives_cli_exposes_query_only_replay_help() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "run_canonical_derivatives_admission.py"), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--contract-database" in completed.stdout
    assert "--chain-database" in completed.stdout
    assert "--as-of" in completed.stdout
