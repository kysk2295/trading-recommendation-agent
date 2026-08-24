from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
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
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_projection import read_opportunity_snapshots
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceiptKind,
    KisKrMinuteProjectionInput,
    KisKrSnapshotProjectionInput,
)
from trading_agent.kis_kr_market_projection import project_kis_kr_completed_minutes, project_kis_kr_market_snapshot
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_decision_models import KrDayDecisionEvent
from trading_agent.kr_day_decision_service import run_kr_day_decision_tick
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text
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
            return local.weekday() >= 5 or not dt.time(9) <= local.time() < dt.time(15, 30)


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
    try:
        capsule_ids = _kr_active_capsule_ids(config.experiment_ledger, now)
        if not capsule_ids:
            return 2, "capsule_authority_missing", ()
        paths = _materialize_kr_requests(config, now, capsule_ids)
        requests = tuple(KrDayCapsuleEvaluationRequest.model_validate_json(read_private_text(path)) for path in paths)
        decisions = run_kr_day_decision_tick(
            requests,
            KrDayDecisionStore(config.state_root / "kr-day-decisions.sqlite3"),
        )
    except (OSError, TypeError, ValidationError, ValueError):
        return 2, "source_invalid", ()
    command = (
        sys.executable,
        str(config.project_root / "run_kr_day_capsule_shadow.py"),
        *(value for path in paths[-3:] for value in ("--request", str(path))),
        "--store",
        str(config.state_root / "kr-day-capsule-shadow.sqlite3"),
        "--output",
        str(config.state_root / "receipts"),
    )
    completed = _run_child(command)
    try:
        payload = json.loads(completed.stdout)
        reason = str(payload.get("result", "kr_capsule_blocked"))
    except (json.JSONDecodeError, AttributeError):
        reason = "kr_capsule_child_invalid"
    return completed.returncode, reason, decisions


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


def _materialize_kr_requests(
    config: KrDaySessionServiceConfig,
    evaluated_at: dt.datetime,
    capsule_ids: tuple[str, ...],
) -> tuple[Path, ...]:
    local_date = evaluated_at.astimezone(_KST).date()
    cycle_prefix = f"kr-research-{local_date.strftime('%Y%m%d')}-"
    cycles = tuple(
        path
        for path in sorted(config.source_root.iterdir())[-24:]
        if path.is_dir() and path.name.startswith(cycle_prefix)
    )
    opportunities = tuple(
        opportunity
        for cycle in reversed(cycles)
        for opportunity in reversed(read_opportunity_snapshots(cycle / "projection" / "opportunities.v1.jsonl"))
        if opportunity.observed_at <= evaluated_at < opportunity.valid_until
    )
    calendars = tuple(
        item
        for item in KisKrSessionCalendarStore(config.calendar_store).snapshots()
        if item.payload.base_date == local_date and item.payload.observed_at <= evaluated_at
    )
    if not opportunities or not calendars:
        raise ValueError
    opportunity = opportunities[0]
    symbol = opportunity.candidates[0].symbol
    cycle_ids = tuple(item.record_id for item in opportunity.evidence_refs if item.namespace == "kr/collection_cycle")
    if len(cycle_ids) != 1:
        raise ValueError
    cycle = next((item for item in cycles if item.name == cycle_ids[0]), None)
    if cycle is None:
        raise ValueError
    receipts = tuple(
        item
        for item in KisKrMarketReceiptStore(cycle / f"{symbol}.market.sqlite3").receipts()
        if item.symbol == symbol and item.received_at <= evaluated_at
    )
    minute_receipts = tuple(item for item in receipts if item.kind is KisKrMarketReceiptKind.MINUTE_BARS)
    prices = tuple(item for item in receipts if item.kind is KisKrMarketReceiptKind.PRICE_STATUS)
    quotes = tuple(item for item in receipts if item.kind is KisKrMarketReceiptKind.ORDER_BOOK)
    if not minute_receipts or not prices or not quotes:
        raise ValueError
    bars = project_kis_kr_completed_minutes(
        KisKrMinuteProjectionInput(receipts=minute_receipts, evaluated_at=evaluated_at)
    )
    market = project_kis_kr_market_snapshot(
        KisKrSnapshotProjectionInput(
            price_receipt=prices[-1],
            quote_receipt=quotes[-1],
            evaluated_at=evaluated_at,
        )
    )
    ledger = ExperimentLedgerStore(config.experiment_ledger)
    capsules = tuple(ledger.day_strategy_capsule(item) for item in capsule_ids[:3])
    if any(item is None for item in capsules):
        raise ValueError
    root = config.state_root / "materialized_requests" / local_date.isoformat()
    paths: list[Path] = []
    for stored in capsules:
        if stored is None:
            raise ValueError
        request = KrDayCapsuleEvaluationRequest(
            capsule=stored.capsule,
            calendar=calendars[-1],
            opportunity=opportunity,
            market=market,
            bars=bars,
            evaluated_at=evaluated_at,
            max_slippage_bps=Decimal("20"),
        )
        canonical = canonical_experiment_ledger_json(request)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        bar = request.bars[-1].end_at.astimezone(_KST).strftime("%H%M%S")
        path = root / f"{stored.capsule.capsule_id}-{bar}-{digest}.json"
        _ = publish_private_immutable_text(path, canonical + "\n")
        paths.append(path)
    return tuple(paths)


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
