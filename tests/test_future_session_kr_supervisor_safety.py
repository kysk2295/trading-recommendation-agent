from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.future_session_kr_support import kr_authority_files
from tests.test_future_session_kr_supervisor import _calendar_receipt, _opportunity
from trading_agent.future_session_kr_materializer import materialize_kr_future_session
from trading_agent.future_session_kr_materializer_models import (
    KrFutureSessionMaterializationRequest,
)
from trading_agent.future_session_kr_supervisor import run_kr_future_session_supervisor
from trading_agent.future_session_kr_supervisor_models import (
    KrFutureSessionSupervisorState,
    KrSupervisorCycleOutcome,
    KrSupervisorPhase,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore


def test_late_same_session_restart_incidents_before_backdated_commands(tmp_path: Path) -> None:
    # Given
    request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=tmp_path / "LaunchAgents",
        )
    )
    calls: list[tuple[str, ...]] = []
    now = dt.datetime.combine(
        plan.target_session,
        dt.time(16, 30),
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    )

    # When
    result = run_kr_future_session_supervisor(
        manifest_path,
        runner=lambda command: calls.append(command) or 0,
        clock=lambda: now,
    )

    # Then
    assert result.result == "incident"
    assert calls == []
    assert request.delivery_database is not None
    events = HermesDeliveryStore(request.delivery_database).events()
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT


def test_multiple_same_cycle_opportunities_incident_without_post_or_onboard(tmp_path: Path) -> None:
    # Given
    request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=tmp_path / "LaunchAgents",
        )
    )
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        name = Path(command[0]).name
        if name == "run_kis_kr_session_calendar_collect.py":
            receipt = _calendar_receipt(plan.target_session)
            store = KisKrSessionCalendarStore(Path(command[command.index("--calendar-store") + 1]))
            _ = store.append(receipt, project_kis_kr_session_calendar(receipt))
        if name == "run_kr_same_cycle_opportunity.py":
            cycle_id = command[command.index("--collection-cycle-id") + 1]
            first = _opportunity(cycle_id)
            second = first.model_copy(update={"opportunity_id": "KR-FUTURE-OPPORTUNITY-002"})
            outbox = Path(command[command.index("--projection-output-dir") + 1]) / "opportunities.v1.jsonl"
            outbox.parent.mkdir(mode=0o700, parents=True)
            outbox.write_text(f"{first.model_dump_json()}\n{second.model_dump_json()}\n", encoding="utf-8")
            outbox.chmod(0o600)
        return 0

    epoch = [int(plan.jobs[0].run_at.timestamp())]

    def clock() -> dt.datetime:
        return dt.datetime.fromtimestamp(epoch[0], tz=dt.UTC)

    def sleeper(seconds: float) -> None:
        epoch[0] += int(seconds)

    # When
    result = run_kr_future_session_supervisor(manifest_path, runner=runner, clock=clock, sleeper=sleeper)

    # Then
    assert result.result == "incident"
    assert any(Path(command[0]).name == "run_kr_same_cycle_opportunity.py" for command in calls)
    assert all(Path(command[0]).name != "run_kr_theme_day_post_session.py" for command in calls)
    assert all("onboard" not in command for command in calls)
    assert request.delivery_database is not None
    events = HermesDeliveryStore(request.delivery_database).events()
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT


def test_supervisor_state_rejects_non_prefix_phase_geometry() -> None:
    # Given / When / Then
    with pytest.raises(ValueError):
        _ = KrFutureSessionSupervisorState(
            manifest_sha256="f" * 64,
            completed_phases=(KrSupervisorPhase.CYCLE,),
            cycle_outcome=KrSupervisorCycleOutcome.READY,
        )


def test_terminal_replay_rejects_changed_rollover_authority(tmp_path: Path) -> None:
    # Given
    request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=tmp_path / "LaunchAgents",
        )
    )
    epoch = [int(plan.jobs[0].run_at.timestamp())]

    def clock() -> dt.datetime:
        return dt.datetime.fromtimestamp(epoch[0], tz=dt.UTC)

    def sleeper(seconds: float) -> None:
        epoch[0] += int(seconds)

    def runner(command: tuple[str, ...]) -> int:
        if Path(command[0]).name == "run_kis_kr_session_calendar_collect.py":
            receipt = _calendar_receipt(plan.target_session)
            store = KisKrSessionCalendarStore(Path(command[command.index("--calendar-store") + 1]))
            _ = store.append(receipt, project_kis_kr_session_calendar(receipt))
        return 0

    terminal = run_kr_future_session_supervisor(
        manifest_path,
        runner=runner,
        clock=clock,
        sleeper=sleeper,
    )
    assert terminal.result == "terminal_no_recommendation"
    assert request.kr_rollover_bundle is not None
    request.kr_rollover_bundle.write_text("{}\n", encoding="utf-8")
    request.kr_rollover_bundle.chmod(0o600)

    # When / Then
    with pytest.raises(ValueError):
        _ = run_kr_future_session_supervisor(
            manifest_path,
            runner=runner,
            clock=clock,
            sleeper=sleeper,
        )
