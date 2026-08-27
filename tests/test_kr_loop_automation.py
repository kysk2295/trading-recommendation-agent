from __future__ import annotations

import datetime as dt
import os
import plistlib
import shutil
from pathlib import Path

from tests.kr_day_close_service_support import close_fixture
from tests.test_kr_loop_engineer_cli import _bundle, _memory_record
from tests.test_kr_loop_engineer_mutation import _EditingWorker
from tests.test_kr_loop_evaluation import _outcome
from tests.test_research_agent_service_cli import _config
from trading_agent.autonomous_memory_models import AutonomousMemoryRecord
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_operator_paths import kr_autonomous_operator_paths
from trading_agent.kr_autonomous_outcome_memory import outcome_record
from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle, kr_loop_engineer_bundle_id
from trading_agent.kr_loop_active_release import bootstrap_active_release, load_active_release, replace_active_release
from trading_agent.kr_loop_automation_config import (
    KrLoopAutomationConfig,
    load_kr_loop_automation_config,
    write_kr_loop_automation_config,
)
from trading_agent.kr_loop_automation_service import completed_kr_session, run_automation_tick
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationExecutor
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_engineer_sync import sync_kr_loop_bundles
from trading_agent.kr_loop_launchd import (
    install_kr_loop_launch_agents,
    provision_kr_loop_launch_agents,
    verify_kr_loop_launch_agents,
)
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    load_research_agent_service_config,
    write_research_agent_service_config,
)
from trading_agent.research_agent_service_health import ResearchAgentServiceHealthEvaluation

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "a" * 40


def test_private_config_and_launchd_contract_bind_active_runtime_and_post_close_schedule(tmp_path: Path) -> None:
    config, config_path = _automation_config(tmp_path)
    active = bootstrap_active_release(config.repository, _head(config.repository), dt.datetime.now(dt.UTC))
    assert replace_active_release(config.active_release, active)

    paths = provision_kr_loop_launch_agents(config, config_path)
    verification = verify_kr_loop_launch_agents(config_path)
    research = plistlib.loads(paths.research_agent.read_bytes())
    loop = plistlib.loads(paths.loop_engineer.read_bytes())

    assert load_kr_loop_automation_config(config_path) == config
    assert verification.ready is True
    assert research["Label"] == "ai.trading-agent.research-agent-runtime"
    assert "run_active_research_agent_runtime.py" in " ".join(research["ProgramArguments"])
    assert research["ProgramArguments"][-2:] == ["--config", str(config.research_agent_config)]
    assert loop["Label"] == "ai.trading-agent.kr-loop-automation"
    assert loop["RunAtLoad"] is False
    assert loop["StartCalendarInterval"] == [
        {"Hour": hour, "Minute": minute, "Weekday": weekday}
        for weekday in range(2, 7)
        for hour, minute in ((16, 30), (18, 30))
    ]


def test_automation_tick_is_closed_before_market_close_and_idle_after_close_without_evidence(tmp_path: Path) -> None:
    config, _ = _automation_config(tmp_path, with_calendar=True)
    pre_close = dt.datetime(2026, 8, 24, 15, 20, tzinfo=KST)
    post_close = dt.datetime(2026, 8, 24, 16, 30, tzinfo=KST)

    assert completed_kr_session(config, pre_close) is None
    assert completed_kr_session(config, post_close) == post_close.date()
    before = run_automation_tick(config, pre_close, commit_reader=lambda _root: BASE)
    after = run_automation_tick(config, post_close, commit_reader=lambda _root: BASE)

    assert before.status == "session_unavailable"
    assert after.status == "idle"
    assert after.session_date == post_close.date()
    assert after.mutated_candidate_id is None
    assert after.shadow_candidate_id is None


def test_launchd_install_replaces_research_runtime_then_bootstraps_loop_job(tmp_path: Path) -> None:
    config, config_path = _automation_config(tmp_path)
    assert replace_active_release(
        config.active_release,
        bootstrap_active_release(config.repository, _head(config.repository), dt.datetime.now(dt.UTC)),
    )
    paths = provision_kr_loop_launch_agents(config, config_path)
    current = tmp_path / "current.plist"
    current.write_text("fixture\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    assert install_kr_loop_launch_agents(
        config_path,
        current,
        runner=lambda command: calls.append(command) or 0,
        health_evaluator=lambda _config, _started, _now: ResearchAgentServiceHealthEvaluation(
            accepted=True,
            state="healthy",
            reason="fresh_matching_ready",
            health=None,
        ),
    )

    assert calls[0][-1] == str(current.absolute())
    assert calls[1][-1] == str(paths.research_agent)
    assert calls[-1][-1] == str(paths.loop_engineer)


def test_automation_runs_two_real_shadow_lanes_then_promotes_and_cuts_over(tmp_path: Path) -> None:
    config, _ = _automation_config(tmp_path, with_calendar=True)
    research = load_research_agent_service_config(config.research_agent_config)
    paths = kr_autonomous_operator_paths(research)
    assert paths is not None
    created_at = dt.datetime(2026, 8, 23, 18, 0, tzinfo=KST)
    source = _bundle()
    draft = source.model_copy(update={"bundle_id": "", "created_at": created_at})
    bundle = KrLoopEngineerEvidenceBundle.model_validate(
        draft.model_copy(update={"bundle_id": kr_loop_engineer_bundle_id(draft)}).model_dump(mode="python")
    )
    source_record = _memory_record(bundle)
    record = AutonomousMemoryRecord.model_validate(
        source_record.model_dump(mode="python", exclude={"memory_id"}) | {"recorded_at": created_at}
    )
    with AutonomousMemoryStore(paths.memory_database).writer() as writer:
        assert writer.append(record)
    base = _main_head(config.repository)
    assert sync_kr_loop_bundles(paths, base_commit=base, now=created_at).inserted == 1
    changed = mutation_contract(bundle, base).allowed_paths[0]
    controller = KrLoopEngineerController(
        KrLoopEngineerStore(paths.loop_database),
        KrLoopMutationExecutor(
            repository=config.repository,
            task_root=paths.loop_task_root,
            artifact_root=paths.loop_artifact_root,
            worker=_EditingWorker(changed),
        ),
    )
    shadowing = controller.mutate(bundle, now=created_at + dt.timedelta(minutes=1))
    assert shadowing.state.value == "shadowing"
    assert replace_active_release(
        config.active_release,
        bootstrap_active_release(config.repository, _head(config.repository), created_at),
    )
    launchctl: list[tuple[str, ...]] = []

    def shadow_runner(command: tuple[str, ...], _environment: dict[str, str]) -> int:
        lane = load_research_agent_service_config(Path(command[-1]))
        session = dt.date.fromisoformat(Path(command[-1]).parents[1].name)
        clusters = 1 if "champion" in str(lane.output_root) else 3
        observed = dt.datetime.combine(session, dt.time(15, 50), tzinfo=KST)
        memory = AutonomousMemoryStore(lane.output_root / "autonomous-supervisor" / "memory.sqlite3")
        outcome = _outcome(str(clusters), clusters=clusters, observed_at=observed)
        item = outcome_record(memory, outcome)
        assert item is not None
        with memory.writer() as writer:
            assert writer.append(item)
        return 0

    first = run_automation_tick(
        config,
        dt.datetime(2026, 8, 24, 16, 30, tzinfo=KST),
        commit_reader=lambda _root: base,
        shadow_runner=shadow_runner,
        launchctl_runner=lambda command: launchctl.append(command) or 0,
    )
    second = run_automation_tick(
        config,
        dt.datetime(2026, 8, 26, 16, 30, tzinfo=KST),
        commit_reader=lambda _root: base,
        shadow_runner=shadow_runner,
        launchctl_runner=lambda command: launchctl.append(command) or 0,
    )

    assert first.status == "shadowed"
    assert second.status == "promoted"
    assert second.release_action == "promote"
    assert load_active_release(config.active_release).action == "candidate"
    assert len(launchctl) == 1


def _automation_config(tmp_path: Path, *, with_calendar: bool = False):
    repository = Path(__file__).resolve().parents[1]
    research = _config(tmp_path / "research", project_root=repository)
    if with_calendar:
        fixture = close_fixture(tmp_path / "calendar")
        sources = research.source_paths.model_copy(update={"kr_calendar_store": fixture.config.calendar_store})
        research = ResearchAgentServiceConfig.model_validate(
            research.model_copy(
                update={
                    "schema_version": 4,
                    "browser_gateway_config": (tmp_path / "browser.json").absolute(),
                    "kr_market_receipt_root": (tmp_path / "market-receipts").absolute(),
                    "kr_social_signal_database": (tmp_path / "social.sqlite3").absolute(),
                    "source_paths": sources,
                }
            ).model_dump(mode="python")
        )
    research_path = (tmp_path / "private" / "research.json").absolute()
    assert write_research_agent_service_config(research_path, research)
    config = KrLoopAutomationConfig(
        repository=repository,
        output_root=(tmp_path / "output").absolute(),
        research_agent_config=research_path,
        active_release=(tmp_path / "private" / "active-release.json").absolute(),
        launch_agents_directory=(tmp_path / "LaunchAgents").absolute(),
        uv_path=Path(shutil.which("uv") or "/bin/false").resolve(),
        grok_binary=Path("/usr/bin/false"),
    )
    config_path = (tmp_path / "private" / "loop-automation.json").absolute()
    assert write_kr_loop_automation_config(config_path, config)
    return config, config_path


def _head(repository: Path) -> str:
    import subprocess

    return subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    ).stdout.strip()


def _main_head(repository: Path) -> str:
    import subprocess

    return subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "refs/heads/main"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
