from __future__ import annotations

import subprocess
from pathlib import Path

REPORT_NAME = "paper_smoke_eligibility_ko.md"


def run_eligibility_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    return subprocess.run(
        (
            "uv",
            "run",
            "python",
            str(root / "run_us_day_paper_smoke_eligibility.py"),
            *arguments,
        ),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def run_isolated_eligibility_cli(
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    return subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(root / "run_us_day_paper_smoke_eligibility.py"),
            *arguments,
        ),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
