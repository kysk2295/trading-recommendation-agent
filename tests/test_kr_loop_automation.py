from __future__ import annotations

import datetime as dt
import plistlib
from pathlib import Path

from tests.kr_loop_automation_support import append_current_calendar, automation_config, head, main_head
from tests.test_kr_loop_engineer_cli import _bundle, _memory_record
from tests.test_kr_loop_engineer_mutation import _EditingWorker
from tests.test_kr_loop_evaluation import _outcome
from trading_agent.autonomous_memory_models import AutonomousMemoryRecord
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_operator_paths import kr_autonomous_operator_paths
from trading_agent.kr_autonomous_outcome_memory import outcome_record
from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle, kr_loop_engineer_bundle_id
from trading_agent.kr_loop_active_release import bootstrap_active_release, load_active_release, replace_active_release
from trading_agent.kr_loop_automation_config import load_kr_loop_automation_config
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
from trading_agent.research_agent_service_config import load_research_agent_service_config
from trading_agent.research_agent_service_health import ResearchAgentServiceHealthEvaluation

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "a" * 40


def test_private_config_and_launchd_contract_bind_active_runtime_and_post_close_schedule(tmp_path: Path) -> None:
    config, config_path = automation_config(tmp_path)
    active = bootstrap_active_release(config.repository, head(config.repository), dt.datetime.now(dt.UTC))
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
    config, _ = automation_config(tmp_path, with_calendar=True)
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


def test_current_base_date_calendar_supersedes_historical_forecast_rows(tmp_path: Path) -> None:
    config, _ = automation_config(tmp_path, with_calendar=True)
    research = load_research_agent_service_config(config.research_agent_config)
    calendar = research.source_paths.kr_calendar_store
    assert calendar is not None
    base_date = dt.date(2026, 8, 26)
    append_current_calendar(calendar, base_date)

    assert completed_kr_session(config, dt.datetime(2026, 8, 26, 16, 30, tzinfo=KST)) == base_date


def test_launchd_install_replaces_research_runtime_then_bootstraps_loop_job(tmp_path: Path) -> None:
    config, config_path = automation_config(tmp_path)
    assert replace_active_release(
        config.active_release,
        bootstrap_active_release(config.repository, head(config.repository), dt.datetime.now(dt.UTC)),
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
    config, _ = automation_config(tmp_path, with_calendar=True)
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
    base = main_head(config.repository)
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
        bootstrap_active_release(config.repository, head(config.repository), created_at),
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
    calendar = research.source_paths.kr_calendar_store
    assert calendar is not None
    append_current_calendar(calendar, dt.date(2026, 8, 26))
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
