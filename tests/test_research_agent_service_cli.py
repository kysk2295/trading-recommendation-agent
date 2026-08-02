from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from run_research_agent_runtime import main
from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    ResearchAgentServiceConfig,
    verify_research_agent_launch_agent,
    write_research_agent_launch_agent,
    write_research_agent_service_config,
)
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_agent_systematic import SystematicResearchActionConfig

WORKTREE = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, *, project_root: Path = WORKTREE) -> ResearchAgentServiceConfig:
    outputs = tmp_path / "outputs"
    sources = ResearchAgentSourcePaths(
        outputs_root=outputs,
        market_context_root=outputs / "market-context",
        day_session_root=outputs / "live-sessions",
        swing_shadow_database=outputs / "swing" / "shadow.sqlite3",
        swing_review_database=outputs / "swing" / "review.sqlite3",
        experiment_ledger=outputs / "experiments" / "ledger.sqlite3",
        lane_review_database=outputs / "reviews" / "lane.sqlite3",
    )
    uv_path = Path(shutil.which("uv") or "/bin/false").resolve()
    systematic = SystematicResearchActionConfig(
        project_root=project_root,
        uv_executable=uv_path,
        python_executable=Path(sys.executable).resolve(),
        context=tmp_path / "systematic" / "context.json",
        response_fixture=None,
        hermes_executable=Path("/bin/echo"),
        model_id="fixture-service-v1",
        experiment_ledger=sources.experiment_ledger,
        receipt_root=tmp_path / "systematic" / "receipts",
        strategy_root=tmp_path / "systematic" / "strategies",
        manifest_root=tmp_path / "systematic" / "manifests",
        queue_root=tmp_path / "systematic" / "queue",
        input_csv=tmp_path / "systematic" / "input.csv",
        data_foundation_manifest=tmp_path / "systematic" / "foundation.json",
        artifact_root=tmp_path / "systematic" / "artifacts",
        review_root=tmp_path / "systematic" / "reviews",
        runs_root=tmp_path / "systematic" / "runs",
        max_runtime_seconds=120.0,
    )
    return ResearchAgentServiceConfig(
        label=RESEARCH_AGENT_SERVICE_LABEL,
        project_root=project_root,
        uv_path=uv_path,
        hermes_executable=Path("/bin/echo"),
        model_id="fixture-service-v1",
        cycle_database=tmp_path / "state" / "cycles.sqlite3",
        output_root=tmp_path / "state" / "reports",
        hermes_database=tmp_path / "state" / "hermes.sqlite3",
        source_paths=sources,
        systematic=systematic,
    )


def _provision(tmp_path: Path, *, project_root: Path = WORKTREE) -> tuple[Path, Path]:
    config = _config(tmp_path, project_root=project_root)
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    plist_path = (tmp_path / "private" / "runtime.plist").absolute()
    assert write_research_agent_service_config(config_path, config)
    assert write_research_agent_launch_agent(plist_path, config, config_path)
    return config_path, plist_path


def test_plist_contains_one_keepalive_service_and_no_secrets(tmp_path: Path) -> None:
    config_path, plist_path = _provision(tmp_path)
    payload = plistlib.loads(plist_path.read_bytes())

    assert verify_research_agent_launch_agent(config_path, plist_path).ready
    assert payload["Label"] == RESEARCH_AGENT_SERVICE_LABEL
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert payload["StandardOutPath"] == payload["StandardErrorPath"] == "/dev/null"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
    text = plist_path.read_text(encoding="utf-8")
    assert "API_KEY" not in text
    assert "TOKEN" not in text
    assert "account" not in text.lower()


def test_activation_rejects_worktree_before_launchctl(tmp_path: Path) -> None:
    config_path, plist_path = _provision(tmp_path)
    calls: list[tuple[str, ...]] = []

    code = main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda argv: calls.append(argv) or 0,
    )

    assert code == 2
    assert calls == []


def test_help_and_bad_config_are_fail_closed(tmp_path: Path) -> None:
    assert main(("--help",)) == 0
    assert main(("verify", "--config", str(tmp_path / "missing"), "--plist", str(tmp_path / "missing"))) == 2


def test_activation_calls_exact_bootstrap_and_kickstart_on_clean_current_main(tmp_path: Path) -> None:
    repository = tmp_path / "main"
    repository.mkdir()
    for name in ("run_research_agent_runtime.py", "run_autonomous_research_cycle.py"):
        (repository / name).write_text("pass\n", encoding="utf-8")
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Research Runtime Test")
    _git(repository, "config", "user.email", "runtime@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/remotes/origin/main", head)
    config_path, plist_path = _provision(tmp_path, project_root=repository)
    calls: list[tuple[str, ...]] = []

    code = main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda argv: calls.append(argv) or 0,
    )

    domain = f"gui/{os.getuid()}"
    assert code == 0
    assert calls == [
        ("/bin/launchctl", "bootstrap", domain, str(plist_path)),
        ("/bin/launchctl", "kickstart", f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"),
    ]


def test_status_reads_missing_journal_without_model_or_broker_activity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, plist_path = _provision(tmp_path)

    code = main(("status", "--config", str(config_path), "--plist", str(plist_path)))

    assert code == 0
    captured = capsys.readouterr()
    assert '"status":"unavailable"' in captured.out
    assert '"model_calls":0' in captured.out
    assert '"broker_mutation":0' in captured.out


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
