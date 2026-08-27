from __future__ import annotations

import os
import sys
from pathlib import Path

import run_active_research_agent_runtime as cli
from tests.test_kr_loop_active_release import NOW, _release
from trading_agent.kr_loop_active_release import KrLoopActiveRelease, replace_active_release


def test_launcher_executes_only_the_verified_active_source(tmp_path: Path) -> None:
    repository, artifacts, candidate = _release(tmp_path)
    artifact = artifacts.verified(candidate.candidate_id)
    active = KrLoopActiveRelease(
        generation=1,
        release_id="8" * 64,
        candidate_id=candidate.candidate_id,
        action="candidate",
        source_root=artifact.candidate_root,
        active_commit=artifact.candidate_commit,
        applied_at=NOW,
    )
    active_path = tmp_path / "active.json"
    assert replace_active_release(active_path, active)
    captured: list[tuple[tuple[str, ...], dict[str, str]]] = []

    exit_code = cli.main(
        (
            "run",
            "--active-release",
            str(active_path),
            "--repository",
            str(repository),
            "--artifact-root",
            str(artifacts.root),
            "--config",
            str(tmp_path / "service.json"),
        ),
        executor=lambda command, environment: captured.append((command, environment)),
    )

    assert exit_code == 0
    command, environment = captured[0]
    assert command == (
        sys.executable,
        str(artifact.candidate_root / "run_research_agent_runtime.py"),
        "run",
        "--config",
        str(tmp_path / "service.json"),
    )
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONPATH"].split(os.pathsep)[0] == str(artifact.candidate_root)


def test_launcher_fails_closed_for_missing_authority(tmp_path: Path) -> None:
    assert cli.main(("run", "--active-release", str(tmp_path / "missing"), "--repository", str(tmp_path))) == 2
