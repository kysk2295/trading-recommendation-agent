from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from tests.future_session_kr_support import kr_authority_files
from trading_agent.future_session_kr_materializer import materialize_kr_future_session
from trading_agent.future_session_kr_materializer_models import (
    KrFutureSessionMaterializationRequest,
)
from trading_agent.future_session_kr_supervisor import run_kr_future_session_supervisor
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)


def test_no_op_source_cycle_terminalizes_once_and_restart_replays(
    tmp_path: Path,
) -> None:
    # Given
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path)
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
        if Path(command[0]).name == "run_kis_kr_session_calendar_collect.py":
            receipt = _calendar_receipt(plan.target_session)
            store = KisKrSessionCalendarStore(Path(command[command.index("--calendar-store") + 1]))
            _ = store.append(receipt, project_kis_kr_session_calendar(receipt))
        return 0

    epoch = [int(plan.jobs[0].run_at.timestamp())]

    def clock() -> dt.datetime:
        return dt.datetime.fromtimestamp(epoch[0], tz=dt.UTC)

    def sleeper(seconds: float) -> None:
        epoch[0] += int(seconds)

    # When
    first = run_kr_future_session_supervisor(manifest_path, runner=runner, clock=clock, sleeper=sleeper)
    replay = run_kr_future_session_supervisor(manifest_path, runner=runner, clock=clock, sleeper=sleeper)

    # Then
    assert first.result == "terminal_no_recommendation"
    assert replay == first
    assert [Path(command[0]).name for command in calls] == [
        "run_kis_kr_session_calendar_collect.py",
        "run_kr_theme_day_composite.py",
        "run_kr_theme_day_trial.py",
        "run_kr_theme_day_trial.py",
        "run_kr_same_cycle_opportunity.py",
        "run_kr_theme_day_post_session.py",
    ]
    state = json.loads((manifest_path.parent / "kr-supervisor-state.json").read_text(encoding="utf-8"))
    assert state["result"] == "terminal_no_recommendation"
    assert state["provider_mutations"] == 0
    assert (manifest_path.parent / "kr-supervisor-report.json").stat().st_mode & 0o777 == 0o600


def _calendar_receipt(session_date: dt.date) -> KisKrSessionCalendarReceipt:
    observed_at = dt.datetime.combine(
        session_date,
        dt.time(8, 45),
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    )
    payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "success",
        "ctx_area_fk": "",
        "ctx_area_nk": "",
        "output": [
            {
                "bass_dt": session_date.strftime("%Y%m%d"),
                "wday_dvsn_cd": "3",
                "bzdy_yn": "Y",
                "tr_day_yn": "Y",
                "opnd_yn": "Y",
                "sttl_day_yn": "Y",
            }
        ],
    }
    return KisKrSessionCalendarReceipt(
        base_date=session_date,
        received_at=observed_at,
        status_code=200,
        content_type="application/json",
        raw_payload=json.dumps(payload, separators=(",", ":")).encode(),
    )


def test_unique_opportunity_onboards_ticks_and_verifies(tmp_path: Path) -> None:
    # Given
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path)
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
            outbox = Path(command[command.index("--projection-output-dir") + 1]) / "opportunities.v1.jsonl"
            outbox.parent.mkdir(mode=0o700, parents=True)
            outbox.write_text(_opportunity(cycle_id).model_dump_json() + "\n", encoding="utf-8")
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
    assert result.result == "terminal_verified"
    assert [Path(command[0]).name for command in calls][-5:] == [
        "run_kr_theme_day_session.py",
        "run_kr_theme_day_session.py",
        "run_kr_theme_day_session.py",
        "run_kr_theme_day_session.py",
        "run_kr_theme_day_session_verify.py",
    ]


def _opportunity(cycle_id: str) -> OpportunitySnapshot:
    observed = dt.datetime(2026, 7, 22, 9, 5, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    return OpportunitySnapshot(
        opportunity_id="KR-FUTURE-OPPORTUNITY-001",
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=observed,
        valid_until=observed + dt.timedelta(minutes=10),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("100"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="kr/collection_cycle", record_id=cycle_id, observed_at=observed),),
        source_coverage=(SourceCoverage(source_id="kr_theme", observed_at=observed, record_count=1, complete=True),),
    )


def test_each_command_waits_for_its_bound_epoch(tmp_path: Path) -> None:
    # Given
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=tmp_path / "LaunchAgents",
        )
    )
    epoch = [int(plan.jobs[0].run_at.timestamp())]
    observed: dict[str, int] = {}

    def clock() -> dt.datetime:
        return dt.datetime.fromtimestamp(epoch[0], tz=dt.UTC)

    def sleeper(seconds: float) -> None:
        epoch[0] += int(seconds)

    def runner(command: tuple[str, ...]) -> int:
        name = Path(command[0]).name
        role = name if name != "run_kr_theme_day_trial.py" else command[1]
        observed[role] = epoch[0]
        if name == "run_kis_kr_session_calendar_collect.py":
            receipt = _calendar_receipt(plan.target_session)
            store = KisKrSessionCalendarStore(Path(command[command.index("--calendar-store") + 1]))
            _ = store.append(receipt, project_kis_kr_session_calendar(receipt))
        return 1 if name == "run_kr_same_cycle_opportunity.py" else 0

    # When
    result = run_kr_future_session_supervisor(
        manifest_path,
        runner=runner,
        clock=clock,
        sleeper=sleeper,
    )

    # Then
    assert result.result == "incident"
    assert observed["run_kr_theme_day_composite.py"] >= int(plan.jobs[1].run_at.timestamp())
    assert observed["start"] >= int(plan.jobs[2].run_at.timestamp())
    assert observed["run_kr_same_cycle_opportunity.py"] >= int(plan.jobs[3].run_at.timestamp())


def test_wrong_session_date_blocks_before_any_command(tmp_path: Path) -> None:
    # Given
    _request, plan, request_path, plan_path = kr_authority_files(tmp_path)
    manifest_path = materialize_kr_future_session(
        KrFutureSessionMaterializationRequest(
            request_path=request_path,
            plan_path=plan_path,
            output_dir=plan.artifact_layout.root,
            launch_agents_dir=tmp_path / "LaunchAgents",
        )
    )
    calls: list[tuple[str, ...]] = []
    wrong_date = plan.target_session + dt.timedelta(days=1)
    now = dt.datetime.combine(wrong_date, dt.time(9), tzinfo=dt.UTC)

    # When
    result = run_kr_future_session_supervisor(
        manifest_path,
        runner=lambda command: calls.append(command) or 0,
        clock=lambda: now,
    )

    # Then
    assert result.result == "incident"
    assert calls == []
    replay = run_kr_future_session_supervisor(
        manifest_path,
        runner=lambda command: calls.append(command) or 0,
        clock=lambda: now,
    )
    assert _request.delivery_database is not None
    events = HermesDeliveryStore(_request.delivery_database).events()
    assert replay == result
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT
