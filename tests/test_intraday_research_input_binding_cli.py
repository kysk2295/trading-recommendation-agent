from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.intraday_research_input_binding_fixtures import (
    NOW,
    write_dataset,
    write_entitlement,
    write_queue,
)

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_intraday_research_input_binding.py"


def test_binding_cli_help_bad_input_and_happy_path(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path)
    queue_path, card_keys = write_queue(tmp_path)
    entitlement_path = write_entitlement(tmp_path)
    help_result = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    bad = subprocess.run(
        (sys.executable, str(SCRIPT), "--strategy-binding", "bad"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--dataset-csv",
            str(dataset.csv_path),
            "--dataset-receipt",
            str(dataset.receipt_path),
            "--entitlement-contract",
            str(entitlement_path),
            "--source-queue-artifact",
            str(queue_path),
            "--strategy-binding",
            f"vwap_reclaim,actual_vwap_reclaim_v1,{card_keys[0]}",
            "--code-version",
            "e" * 40,
            "--registered-at",
            NOW.isoformat(),
            "--output-dir",
            str(tmp_path / "cli-output"),
            "--max-bars",
            "500",
            "--max-sessions",
            "1",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0
    assert "--entitlement-contract" in help_result.stdout
    assert "--strategy-binding" in help_result.stdout
    assert bad.returncode == 2
    assert completed.returncode == 0, completed.stderr
    report = (tmp_path / "cli-output" / "intraday_research_input_binding_ko.md").read_text(encoding="utf-8")
    assert "- result: ready" in report
    assert "- foundations: 1" in report
    assert "- external mutation: 0" in report
