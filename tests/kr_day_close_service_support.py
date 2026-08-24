from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from tests.day_strategy_capsule_support import builtin_request
from tests.kr_day_shadow_support import run_authorized_kr_shadow_tick
from tests.test_day_research_attempt_binding import _attempt, _binding, _family, _manifest, _version
from tests.test_kis_kr_session_calendar import _payload as calendar_payload
from tests.test_kis_kr_session_calendar import _row as calendar_row
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation
from trading_agent.day_forward_trial_identity import ForwardExecutionLane
from trading_agent.day_forward_trial_models import DayForwardTrial
from trading_agent.day_strategy_capsule import publish_day_strategy_capsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt, KrSessionCalendarSnapshot
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation, KrDayCapsuleEvaluationPayload
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_close_service_config import KrDayCloseServiceConfig
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import AttemptStatus

ROOT = Path(__file__).resolve().parents[1]
KST = dt.timezone(dt.timedelta(hours=9))
SESSION_DATE = dt.date(2026, 8, 24)


@dataclass(frozen=True, slots=True)
class CloseFixture:
    config: KrDayCloseServiceConfig
    config_path: Path
    pre_close: dt.datetime
    post_close: dt.datetime


def close_fixture(
    root: Path,
    *,
    open_day: bool = True,
    terminal: bool = True,
    calendar_base_date: dt.date = SESSION_DATE,
    shadow_snapshot_id: str | None = None,
) -> CloseFixture:
    state = root / "state"
    state.mkdir(mode=0o700, parents=True)
    os.chmod(state, 0o700)
    config = KrDayCloseServiceConfig(
        project_root=ROOT,
        expected_commit=_head(),
        executable_path=Path(sys.executable).resolve(),
        state_root=state,
        calendar_store=state / "calendar/calendar.sqlite3",
        experiment_ledger=state / "ledger/experiment.sqlite3",
        report_root=state / "reports",
        policy_root=state / "policies",
        hermes_delivery_database=state / "hermes/delivery.sqlite3",
        health_root=state / "health",
        completion_root=state / "completion",
        launch_agents_directory=root / "LaunchAgents",
    )
    snapshot = _seed_calendar(
        config.calendar_store,
        open_day=open_day,
        base_date=calendar_base_date,
    )
    if open_day:
        _seed_session(
            config,
            snapshot.snapshot_id,
            terminal=terminal,
            shadow_snapshot_id=shadow_snapshot_id,
        )
    return CloseFixture(
        config=config,
        config_path=root / f"kr-day-close-{config.expected_commit}.json",
        pre_close=dt.datetime(2026, 8, 24, 15, 29, tzinfo=KST),
        post_close=dt.datetime(2026, 8, 24, 15, 40, tzinfo=KST),
    )


def _seed_calendar(
    path: Path,
    *,
    open_day: bool,
    base_date: dt.date,
) -> KrSessionCalendarSnapshot:
    flag = "Y" if open_day else "N"
    leading = (
        ()
        if base_date == SESSION_DATE
        else (calendar_row(base_date.strftime("%Y%m%d"), "Y", "Y", "Y", "Y"),)
    )
    receipt = KisKrSessionCalendarReceipt(
        base_date=base_date,
        received_at=dt.datetime.combine(base_date, dt.time(8), tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=calendar_payload(
            rows=(
                *leading,
                calendar_row("20260824", flag, flag, flag, flag),
                calendar_row("20260825", "N", "N", "N", "N"),
                calendar_row("20260826", "Y", "Y", "Y", "Y"),
            )
        ),
    )
    snapshot = project_kis_kr_session_calendar(receipt)
    assert KisKrSessionCalendarStore(path).append(receipt, snapshot)
    return snapshot


def _seed_session(
    config: KrDayCloseServiceConfig,
    snapshot_id: str,
    *,
    terminal: bool,
    shadow_snapshot_id: str | None,
) -> None:
    ledger = ExperimentLedgerStore(config.experiment_ledger)
    family = _family()
    version = _version(family, market_id=MarketId.KR_EQUITIES)
    attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    binding = _binding(attempt, version)
    with ledger.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    capsule, _ = publish_day_strategy_capsule(
        ledger,
        replace(
            builtin_request(market_id=MarketId.KR_EQUITIES),
            hypothesis_version_id=version.hypothesis_version_id,
            attempt_binding_id=binding.binding_id,
            artifact_ref=binding.artifact_ref,
            evaluation_cadence=version.evaluation_cadence,
            entry_rule=version.entry_rule,
            exit_rule=version.exit_rule,
            stop_rule=version.stop_rule,
            cost_model=version.cost_model,
            protocol_sha256=version.protocol_sha256,
            published_at=binding.bound_at + dt.timedelta(minutes=1),
        ),
    )
    eligible = dt.datetime(2026, 8, 24, 10, 1, tzinfo=KST).astimezone(dt.UTC)
    trial_payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": capsule.capsule_id,
        "hypothesis_version_id": capsule.hypothesis_version_id,
        "market_id": MarketId.KR_EQUITIES,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": "XKRX-2026-08-24",
        "session_date": SESSION_DATE,
        "calendar_snapshot_id": f"calendar://official/XKRX/{snapshot_id}",
        "cost_model_sha256": _sha(canonical_experiment_ledger_json(capsule.cost_model)),
        "source_refs_sha256": _sha(json.dumps(version.source_refs, separators=(",", ":"))),
        "evidence_schema_sha256": _sha(json.dumps(capsule.evidence_schema, separators=(",", ":"))),
        "preregistered_at": eligible - dt.timedelta(seconds=30),
        "registration_completed_bar_at": eligible - dt.timedelta(minutes=1),
        "first_eligible_completed_bar_at": eligible,
        "trading_authority": False,
        "profitability_claim": False,
    }
    trial = DayForwardTrial.model_validate(
        trial_payload | {"trial_id": DayForwardTrial.canonical_id_for(trial_payload)}
    )
    with ledger.writer() as writer:
        assert writer.register_day_forward_trial(trial)
    event_snapshot_id = snapshot_id if shadow_snapshot_id is None else shadow_snapshot_id
    entry = _authorized_evaluation(
        capsule.capsule_id,
        capsule.hypothesis_version_id,
        event_snapshot_id,
    )
    shadow = KrDayCapsuleShadowStore(config.shadow_store)
    _ = run_authorized_kr_shadow_tick(shadow, (entry,))
    if terminal:
        _ = run_authorized_kr_shadow_tick(
            shadow,
            (
                _bind_evaluation(
                    _advance(entry, low=Decimal("9900"), high=Decimal("10400")),
                    capsule.capsule_id,
                    capsule.hypothesis_version_id,
                    event_snapshot_id,
                ),
            ),
        )
    generated = KrDayDecisionStore(
        config.shadow_store.with_name(f"{config.shadow_store.stem}-decisions.sqlite3")
    )
    destination = KrDayDecisionStore(config.decision_store)
    for event in generated.events():
        _ = destination.append(event)


def _authorized_evaluation(capsule_id: str, hypothesis_id: str, snapshot_id: str) -> KrDayCapsuleEvaluation:
    return _bind_evaluation(_entry_evaluation(), capsule_id, hypothesis_id, snapshot_id)


def _bind_evaluation(
    source: KrDayCapsuleEvaluation,
    capsule_id: str,
    hypothesis_id: str,
    snapshot_id: str,
) -> KrDayCapsuleEvaluation:
    setup = source.setup_input.model_copy(update={"producer_strategy_version": capsule_id})
    values = source.model_dump(mode="python", exclude={"evaluation_id"}) | {
        "capsule_id": capsule_id,
        "hypothesis_version_id": hypothesis_id,
        "calendar_snapshot_id": snapshot_id,
        "setup_input": setup,
    }
    payload = KrDayCapsuleEvaluationPayload.model_validate(values)
    return KrDayCapsuleEvaluation.model_validate(
        values | {"evaluation_id": KrDayCapsuleEvaluation.canonical_id_for(payload)}
    )


def _head() -> str:
    return subprocess.run(
        ("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = ("KST", "ROOT", "SESSION_DATE", "CloseFixture", "close_fixture")
