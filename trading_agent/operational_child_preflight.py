from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

CORE_CHILDREN: Final = (
    "run_kis_paper_scan.py",
    "run_kis_eod_catchup.py",
    "run_paper_metrics.py",
    "run_daily_research_record.py",
    "run_adaptive_strategy_evaluation.py",
)
ChildRunner = Callable[[Path, Path], int]


def preflight_operational_children(
    project_root: Path,
    optional_children: tuple[str, ...],
    runner: ChildRunner | None = None,
) -> tuple[str, ...]:
    selected_runner = _run_child_help if runner is None else runner
    child_names = tuple(dict.fromkeys((*CORE_CHILDREN, *optional_children)))
    with tempfile.TemporaryDirectory(prefix="trading-agent-preflight-") as raw_working_dir:
        working_dir = Path(raw_working_dir)
        return tuple(
            child_name
            for child_name in child_names
            if selected_runner(project_root / child_name, working_dir) != 0
        )


def _run_child_help(script: Path, working_dir: Path) -> int:
    try:
        completed = subprocess.run(
            (str(script), "--help"),
            cwd=working_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return completed.returncode
