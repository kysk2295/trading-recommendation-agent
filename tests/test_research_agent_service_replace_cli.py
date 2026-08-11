from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from run_research_agent_runtime import main
from tests.research_agent_systematic_input_fixtures import (
    write_blocked_systematic_input_activation,
)
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    ResearchAgentServiceConfig,
    canonical_research_agent_service_config_sha256,
    load_research_agent_service_config,
    write_research_agent_launch_agent,
    write_research_agent_service_config,
)
from trading_agent.research_agent_service_health import (
    ResearchAgentServiceHealth,
    ResearchAgentServiceHealthEvaluation,
    research_agent_service_health_path,
)
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_agent_systematic import SystematicResearchActionConfig


@dataclass(frozen=True, slots=True)
class ReplacementFixture:
    repository: Path
    current_config: Path
    current_plist: Path
    candidate_config: Path
    candidate_plist: Path


def test_replace_help_exposes_only_pair_paths(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(("replace", "--help"))

    assert code == 0
    output = capsys.readouterr().out
    assert all(
        option in output
        for option in ("--current-config", "--current-plist", "--candidate-config", "--candidate-plist")
    )
    assert all(secret not in output.lower() for secret in ("api_key", "token", "account"))


def test_replace_success_calls_exact_order_after_fresh_matching_health(tmp_path: Path) -> None:
    fixture = _replacement_fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    code = main(
        _argv(fixture),
        runner=lambda command: calls.append(command) or 0,
        health_evaluator=_ready_health_evaluator,
    )

    domain = f"gui/{os.getuid()}"
    assert code == 0
    assert calls == [
        ("/bin/launchctl", "bootout", domain, str(fixture.current_plist)),
        ("/bin/launchctl", "bootstrap", domain, str(fixture.candidate_plist)),
        ("/bin/launchctl", "kickstart", f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"),
    ]


def test_replace_captures_cutover_start_before_candidate_launch(tmp_path: Path) -> None:
    # Given: a candidate whose evaluator records when the cutover clock is read.
    fixture = _replacement_fixture(tmp_path)
    events: list[str] = []

    def clock() -> dt.datetime:
        events.append("clock")
        return dt.datetime(2026, 8, 11, 3, 0, tzinfo=dt.UTC)

    def runner(command: tuple[str, ...]) -> int:
        events.append(command[1])
        return 0

    def health_evaluator(
        _config: ResearchAgentServiceConfig,
        _started_at: dt.datetime,
        _evaluated_at: dt.datetime,
    ) -> ResearchAgentServiceHealthEvaluation:
        events.append("health")
        return ResearchAgentServiceHealthEvaluation(
            accepted=True,
            state="healthy",
            reason="fresh_matching_ready",
            health=None,
        )

    # When: replace starts the candidate transition.
    code = main(_argv(fixture), clock=clock, runner=runner, health_evaluator=health_evaluator)

    # Then: confirmed current bootout precedes the candidate freshness boundary.
    assert code == 0
    assert events == ["bootout", "clock", "bootstrap", "kickstart", "clock", "health"]


def test_replace_rejects_old_same_digest_health_written_before_current_bootout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: current and candidate share a digest, and current writes health during its bootout.
    fixture = _replacement_fixture(tmp_path)
    candidate = load_research_agent_service_config(fixture.candidate_config)
    entry_at = dt.datetime(2026, 8, 11, 3, 0, tzinfo=dt.UTC)
    cutover_at = entry_at + dt.timedelta(seconds=2)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("trading_agent.research_agent_service_health.time.sleep", lambda _seconds: None)

    def clock() -> dt.datetime:
        return cutover_at if calls else entry_at

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        if command == ("/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(fixture.current_plist)):
            old_health = ResearchAgentServiceHealth(
                config_sha256=canonical_research_agent_service_config_sha256(candidate),
                observed_at=entry_at + dt.timedelta(seconds=1),
                state="ready",
                reason="runtime_ready",
            )
            write_private_stable_report(
                research_agent_service_health_path(candidate.output_root),
                old_health.model_dump_json() + "\n",
            )
        return 0

    # When: replacement evaluates the persisted health after current bootout.
    code = main(_argv(fixture), clock=clock, runner=runner)

    # Then: old same-digest health is stale rather than candidate-ready.
    assert code == 2
    assert capsys.readouterr().err == "replace_health_not_fresh\n"


def test_replace_unhealthy_candidate_boots_out_and_restores_current(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _replacement_fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    code = main(
        _argv(fixture),
        runner=lambda command: calls.append(command) or 0,
        health_evaluator=_mismatched_health_evaluator,
    )

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"
    assert code == 2
    assert calls == [
        ("/bin/launchctl", "bootout", domain, str(fixture.current_plist)),
        ("/bin/launchctl", "bootstrap", domain, str(fixture.candidate_plist)),
        ("/bin/launchctl", "kickstart", target),
        ("/bin/launchctl", "bootout", domain, str(fixture.candidate_plist)),
        ("/bin/launchctl", "bootstrap", domain, str(fixture.current_plist)),
        ("/bin/launchctl", "kickstart", target),
    ]
    assert capsys.readouterr().err == "replace_health_candidate_mismatch\n"


@pytest.mark.parametrize("restore_failure", ("bootstrap", "kickstart"))
def test_replace_reports_current_restore_failure_with_a_typed_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    restore_failure: str,
) -> None:
    # Given: an unhealthy candidate and a current restore that cannot complete.
    fixture = _replacement_fixture(tmp_path)
    calls: list[tuple[str, ...]] = []
    current_bootstrap = str(fixture.current_plist)
    kickstarts = 0

    def runner(command: tuple[str, ...]) -> int:
        nonlocal kickstarts
        calls.append(command)
        if command[1] == "bootstrap" and command[3] == current_bootstrap:
            return int(restore_failure == "bootstrap")
        if command[1] == "kickstart":
            kickstarts += 1
            return int(restore_failure == "kickstart" and kickstarts == 2)
        return 0

    # When: rollback tries to restore the previous service.
    code = main(
        _argv(fixture),
        runner=runner,
        health_evaluator=_mismatched_health_evaluator,
    )

    # Then: the primary and restore-specific typed reasons are observable.
    assert code == 2
    assert capsys.readouterr().err == (
        "replace_health_candidate_mismatch\n"
        f"replace_current_restore_{restore_failure}_failed\n"
    )
    assert calls[-1][1] == restore_failure


@pytest.mark.parametrize("bad_pair", ["current", "candidate"])
def test_replace_bad_candidate_makes_zero_calls(
    tmp_path: Path,
    bad_pair: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _replacement_fixture(tmp_path)
    bad_plist = {"current": fixture.current_plist, "candidate": fixture.candidate_plist}[bad_pair]
    bad_plist.chmod(0o600)
    bad_plist.write_text("invalid", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    code = main(_argv(fixture), runner=lambda command: calls.append(command) or 0)

    assert code == 2
    assert calls == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_replace_non_main_candidate_makes_zero_calls(tmp_path: Path) -> None:
    fixture = _replacement_fixture(tmp_path)
    _git(fixture.repository, "switch", "-c", "codex/fixture")
    calls: list[tuple[str, ...]] = []

    code = main(_argv(fixture), runner=lambda command: calls.append(command) or 0)

    assert code == 2
    assert calls == []


@pytest.mark.parametrize("failed_operation", ["bootstrap", "kickstart"])
def test_replace_candidate_start_failure_boots_out_candidate_and_restores_current(
    tmp_path: Path,
    failed_operation: str,
) -> None:
    fixture = _replacement_fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        return int(command[1] == failed_operation)

    code = main(_argv(fixture), runner=runner)

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"
    expected = {
        "bootstrap": [
            ("/bin/launchctl", "bootout", domain, str(fixture.current_plist)),
            ("/bin/launchctl", "bootstrap", domain, str(fixture.candidate_plist)),
            ("/bin/launchctl", "bootout", domain, str(fixture.candidate_plist)),
            ("/bin/launchctl", "bootstrap", domain, str(fixture.current_plist)),
        ],
        "kickstart": [
            ("/bin/launchctl", "bootout", domain, str(fixture.current_plist)),
            ("/bin/launchctl", "bootstrap", domain, str(fixture.candidate_plist)),
            ("/bin/launchctl", "kickstart", target),
            ("/bin/launchctl", "bootout", domain, str(fixture.candidate_plist)),
            ("/bin/launchctl", "bootstrap", domain, str(fixture.current_plist)),
            ("/bin/launchctl", "kickstart", target),
        ],
    }
    assert code == 2
    assert calls == expected[failed_operation]


@pytest.mark.parametrize(("probe_code", "expected_code"), [(113, 0), (0, 2), (1, 2)])
def test_replace_bootout_failure_requires_confirmed_absence(
    tmp_path: Path,
    probe_code: int,
    expected_code: int,
) -> None:
    fixture = _replacement_fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        return probe_code if command[1] == "print" else int(command[1] == "bootout")

    code = main(_argv(fixture), runner=runner, health_evaluator=_ready_health_evaluator)

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"
    prefix = [
        ("/bin/launchctl", "bootout", domain, str(fixture.current_plist)),
        ("/bin/launchctl", "print", target),
    ]
    expected = {
        113: [
            *prefix,
            ("/bin/launchctl", "bootstrap", domain, str(fixture.candidate_plist)),
            ("/bin/launchctl", "kickstart", target),
        ],
        0: prefix,
        1: prefix,
    }
    assert code == expected_code
    assert calls == expected[probe_code]


def _replacement_fixture(tmp_path: Path) -> ReplacementFixture:
    repository = _current_main_repository(tmp_path)
    current_config, current_plist = _provision(tmp_path, repository, "current")
    candidate_config, candidate_plist = _provision(tmp_path, repository, "candidate")
    return ReplacementFixture(
        repository=repository,
        current_config=current_config,
        current_plist=current_plist,
        candidate_config=candidate_config,
        candidate_plist=candidate_plist,
    )


def _current_main_repository(tmp_path: Path) -> Path:
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
    return repository


def _provision(tmp_path: Path, repository: Path, name: str) -> tuple[Path, Path]:
    config = _config(tmp_path, repository)
    config_path = (tmp_path / "private" / f"{name}.json").absolute()
    plist_path = (tmp_path / "private" / f"{name}.plist").absolute()
    assert write_research_agent_service_config(config_path, config)
    assert write_research_agent_launch_agent(plist_path, config, config_path)
    return config_path, plist_path


def _config(tmp_path: Path, project_root: Path) -> ResearchAgentServiceConfig:
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
        provider_id="fixture-provider",
        experiment_ledger=sources.experiment_ledger,
        receipt_root=tmp_path / "systematic" / "receipts",
        strategy_root=tmp_path / "systematic" / "strategies",
        manifest_root=tmp_path / "systematic" / "manifests",
        queue_root=tmp_path / "systematic" / "queue",
        input_activation=tmp_path / "systematic" / "input-activation.json",
        artifact_root=tmp_path / "systematic" / "artifacts",
        review_root=tmp_path / "systematic" / "reviews",
        runs_root=tmp_path / "systematic" / "runs",
        max_runtime_seconds=120.0,
    )
    if not systematic.input_activation.exists():
        write_blocked_systematic_input_activation(systematic.input_activation)
    return ResearchAgentServiceConfig(
        label=RESEARCH_AGENT_SERVICE_LABEL,
        project_root=project_root,
        uv_path=uv_path,
        hermes_executable=Path("/bin/echo"),
        model_id="fixture-service-v1",
        provider_id="fixture-provider",
        cycle_database=tmp_path / "state" / "cycles.sqlite3",
        output_root=tmp_path / "state" / "reports",
        hermes_database=tmp_path / "state" / "hermes.sqlite3",
        source_paths=sources,
        systematic=systematic,
    )


def _argv(fixture: ReplacementFixture) -> tuple[str, ...]:
    return (
        "replace",
        "--current-config",
        str(fixture.current_config),
        "--current-plist",
        str(fixture.current_plist),
        "--candidate-config",
        str(fixture.candidate_config),
        "--candidate-plist",
        str(fixture.candidate_plist),
    )


def _ready_health_evaluator(
    config: ResearchAgentServiceConfig,
    _started_at: dt.datetime,
    _evaluated_at: dt.datetime,
) -> ResearchAgentServiceHealthEvaluation:
    return ResearchAgentServiceHealthEvaluation(
        accepted=True,
        state="healthy",
        reason="fresh_matching_ready",
        health=None,
    )


def _mismatched_health_evaluator(
    config: ResearchAgentServiceConfig,
    _started_at: dt.datetime,
    _evaluated_at: dt.datetime,
) -> ResearchAgentServiceHealthEvaluation:
    return ResearchAgentServiceHealthEvaluation(
        accepted=False,
        state="unhealthy",
        reason="candidate_mismatch",
        health=None,
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
