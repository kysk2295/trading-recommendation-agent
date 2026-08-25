from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.day_forward_probe_admission import (
    ForwardProbeQueueItem,
    ForwardProbeSlotRequest,
    select_active_probe_slots,
)
from trading_agent.day_session_service_config import (
    DaySessionServiceConfig,
    KrDaySessionServiceConfig,
    UsDaySessionServiceConfig,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowStatus
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_delivery_record_builders import bound_kr_day_decision_id
from trading_agent.kr_day_decision_models import KrDayDecisionEvent
from trading_agent.kr_day_decision_service import expire_due_kr_day_decisions, run_kr_day_decision_tick
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_day_session_delivery import project_kr_day_session_delivery
from trading_agent.kr_day_session_materializer import (
    materialize_kr_requests as _materialize_kr_requests,
)
from trading_agent.private_immutable_file import read_private_text
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_session_tick import UsDaySessionTickRequest, run_us_day_session_tick
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_strategy_day_input import UsStrategyDayInput

_KST = ZoneInfo("Asia/Seoul")
Clock = Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class DaySessionServiceResult:
    market: Literal["us", "kr"]
    status: Literal["processed", "no_action"]
    reason: str
    mutation: Literal[0] = 0
    decisions: tuple[KrDayDecisionEvent, ...] = ()


def run_day_session_service_tick(
    config: DaySessionServiceConfig,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> DaySessionServiceResult:
    now = clock().astimezone(dt.UTC)
    if _session_is_closed(config, now):
        result = DaySessionServiceResult(config.market, "no_action", "session_closed")
    else:
        authority = _authority_reason(config)
        if authority is not None:
            result = DaySessionServiceResult(config.market, "no_action", authority)
        else:
            match config:
                case UsDaySessionServiceConfig():
                    code, reason = _run_us(config, now)
                    decisions = ()
                case KrDaySessionServiceConfig():
                    code, reason, decisions = _run_kr(config, now)
            status = "processed" if code == 0 else "no_action"
            result = DaySessionServiceResult(config.market, status, reason, 0, decisions)
    _persist_health(config, now, result)
    return result


def _session_is_closed(config: DaySessionServiceConfig, now: dt.datetime) -> bool:
    match config:
        case UsDaySessionServiceConfig():
            bounds = regular_session_bounds(now.astimezone(NEW_YORK).date())
            return bounds is None or not bounds[0] <= now < bounds[1]
        case KrDaySessionServiceConfig():
            local = now.astimezone(_KST)
            return local.weekday() >= 5 or not dt.time(9) <= local.time() < dt.time(15, 32)


def _authority_reason(config: DaySessionServiceConfig) -> str | None:
    try:
        root = config.project_root.resolve(strict=True)
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = _git(root, "rev-parse", "HEAD")
        local_main = _git(root, "rev-parse", "refs/heads/main")
        tracked = _git(root, "status", "--porcelain=v1", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "project_root_invalid"
    if root != config.project_root or branch != "main" or head != local_main or head != config.expected_commit:
        return "commit_mismatch"
    return "tracked_worktree_dirty" if tracked else None


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _run_us(config: UsDaySessionServiceConfig, now: dt.datetime) -> tuple[int, str]:
    session = config.source_root / now.astimezone(NEW_YORK).strftime("%Y%m%d")
    day_input = _latest_day_input(session)
    if day_input is None:
        return 2, "source_missing"
    code, result = run_us_day_session_tick(
        UsDaySessionTickRequest(
            day_input=day_input,
            outputs=config.state_root,
            evaluated_at=now,
            version_store=config.source_root / "version-store.sqlite3",
            production_manifest=config.source_root / "production-manifest.json",
            live_model_provider=config.live_model_provider,
            allow_post_close_source_fallback=False,
        )
    )
    return code, result.reason or result.tick_status or result.status


def _run_kr(
    config: KrDaySessionServiceConfig,
    now: dt.datetime,
) -> tuple[int, str, tuple[KrDayDecisionEvent, ...]]:
    decision_store = KrDayDecisionStore(config.state_root / "kr-day-decisions.sqlite3")
    try:
        expired = expire_due_kr_day_decisions(
            decision_store,
            now,
            _settled_kr_decision_ids(config.state_root, now),
        )
    except ValueError:
        return 2, "decision_store_invalid", ()
    if now.astimezone(_KST).time() >= dt.time(15, 30):
        try:
            _ = project_kr_day_session_delivery(config.state_root, config.hermes_delivery_database)
        except (OSError, RuntimeError, TypeError, ValueError):
            return 2, "decision_delivery_failed", expired
        reason = "decision_expired" if expired else "session_close_no_action"
        return 0, reason, expired
    try:
        capsule_ids = _kr_active_capsule_ids(config.experiment_ledger, now)
        if not capsule_ids:
            if expired:
                _ = project_kr_day_session_delivery(config.state_root, config.hermes_delivery_database)
                return 0, "decision_expired", expired
            return 2, "capsule_authority_missing", ()
        paths = _materialize_kr_requests(config, now, capsule_ids)
        if not paths:
            return 2, "no_opportunity", expired
    except (OSError, RuntimeError, TypeError, ValidationError, ValueError):
        if expired:
            try:
                _ = project_kr_day_session_delivery(config.state_root, config.hermes_delivery_database)
            except (OSError, RuntimeError, TypeError, ValueError):
                return 2, "decision_delivery_failed", expired
        return 2, "source_invalid", expired
    requests: list[KrDayCapsuleEvaluationRequest] = []
    decision_blocked = False
    for path in paths:
        try:
            requests.append(KrDayCapsuleEvaluationRequest.model_validate_json(read_private_text(path)))
        except (OSError, TypeError, ValidationError, ValueError):
            decision_blocked = True
    try:
        decisions = (*expired, *run_kr_day_decision_tick(tuple(requests), decision_store))
    except ValueError:
        decisions = expired
        decision_blocked = True
    command = (
        sys.executable,
        str(config.project_root / "run_kr_day_capsule_shadow.py"),
        *(value for path in paths[-3:] for value in ("--request", str(path))),
        "--store",
        str(config.state_root / "kr-day-capsule-shadow.sqlite3"),
        "--decision-store",
        str(config.state_root / "kr-day-decisions.sqlite3"),
        "--output",
        str(config.state_root / "receipts"),
    )
    completed = _run_child(command)
    try:
        _ = project_kr_day_session_delivery(config.state_root, config.hermes_delivery_database)
    except (OSError, RuntimeError, TypeError, ValueError):
        return 2, "decision_delivery_failed", decisions
    try:
        payload = json.loads(completed.stdout)
        reason = str(payload.get("result", "kr_capsule_blocked"))
    except (json.JSONDecodeError, AttributeError):
        reason = "kr_capsule_child_invalid"
    if completed.returncode == 0 and decision_blocked:
        reason = "shadow_managed_decision_blocked"
    return completed.returncode, reason, decisions


def _settled_kr_decision_ids(state_root: Path, now: dt.datetime) -> frozenset[str]:
    session_date = now.astimezone(_KST).date().isoformat()
    events = KrDayCapsuleShadowStore(state_root / "kr-day-capsule-shadow.sqlite3").events()
    settled = {
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowStatus.STOPPED,
        KrDayCapsuleShadowStatus.TARGETED,
        KrDayCapsuleShadowStatus.CENSORED,
        KrDayCapsuleShadowStatus.BLOCKED,
    }
    return frozenset(
        decision_id
        for event in events
        if event.session_date.isoformat() == session_date and event.status in settled
        if (decision_id := bound_kr_day_decision_id(event)) is not None
    )


def _kr_active_capsule_ids(ledger_path: Path, now: dt.datetime) -> tuple[str, ...]:
    ledger = ExperimentLedgerStore(ledger_path)
    local_date = now.astimezone(_KST).date()
    policies = tuple(
        item
        for item in ledger.day_exploration_policies(MarketId.KR_EQUITIES)
        if item.payload.effective_session_date == local_date
    )
    if policies:
        return policies[-1].payload.active_capsule_ids
    candidates = tuple(
        ForwardProbeQueueItem(trial=state.trial, policy_priority=50, queued_at=state.trial.preregistered_at)
        for state in ledger.day_forward_trials(MarketId.KR_EQUITIES)
        if not state.terminal
        and state.trial.session_date == local_date
        and state.trial.first_eligible_completed_bar_at <= now
    )
    selection = select_active_probe_slots(
        ForwardProbeSlotRequest(
            market_id=MarketId.KR_EQUITIES,
            candidates=candidates,
            active_capsule_ids=(),
        )
    )
    return tuple(item.trial.capsule_id for item in selection.selected)


def _run_child(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(command, 2, "", "")


def _latest_day_input(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    for path in reversed(sorted(root.glob("*.day-input.json"))[-12:]):
        try:
            _ = UsStrategyDayInput.model_validate_json(read_private_text(path))
        except (OSError, TypeError, ValidationError, ValueError):
            continue
        return path
    return None


def _persist_health(
    config: DaySessionServiceConfig,
    observed_at: dt.datetime,
    result: DaySessionServiceResult,
) -> None:
    payload = {
        "market": result.market,
        "mutation": 0,
        "observed_at": observed_at.isoformat(),
        "reason": result.reason,
        "schema_version": 1,
        "source_contract": "typed-collector-artifacts-v1",
        "status": result.status,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    write_private_stable_report(
        config.state_root / "health" / "day_session_service_health.json",
        json.dumps(
            payload | {"receipt_sha256": digest},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
    )


__all__ = ("DaySessionServiceResult", "run_day_session_service_tick")
