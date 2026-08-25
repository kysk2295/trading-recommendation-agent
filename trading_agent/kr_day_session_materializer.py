from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from trading_agent.day_session_service_config import KrDaySessionServiceConfig
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore, StoredDayStrategyCapsule
from trading_agent.hermes_delivery_projection import read_opportunity_snapshots
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceiptKind,
    KisKrMinuteProjectionInput,
    KisKrSnapshotProjectionInput,
)
from trading_agent.kis_kr_market_projection import (
    project_kis_kr_market_snapshot,
    project_kis_kr_recent_completed_minutes,
)
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.signal_contract_models import OpportunitySnapshot

_KST: Final = ZoneInfo("Asia/Seoul")
_MAX_CAPSULES: Final = 3


@dataclass(frozen=True, slots=True)
class _SourceSelection:
    opportunity: OpportunitySnapshot
    calendar: KrSessionCalendarSnapshot
    cycle: Path


def materialize_kr_requests(
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
    outboxes = tuple(
        cycle / "projection" / "opportunities.v1.jsonl"
        for cycle in cycles
        if (cycle / "projection" / "opportunities.v1.jsonl").is_file()
    )
    if not outboxes:
        raise ValueError
    opportunities = tuple(
        opportunity
        for outbox in reversed(outboxes)
        for opportunity in reversed(
            read_opportunity_snapshots(outbox)
        )
        if opportunity.observed_at <= evaluated_at
    )
    if not opportunities:
        return ()
    calendars = tuple(
        item
        for item in KisKrSessionCalendarStore(config.calendar_store).snapshots()
        if item.payload.observed_at <= evaluated_at
        and any(
            day.session_date == local_date
            and day.business_day
            and day.trading_day
            and day.open_day
            for day in item.payload.days
        )
    )
    if not calendars:
        raise ValueError
    ledger = ExperimentLedgerStore(config.experiment_ledger)
    capsules = tuple(
        ledger.day_strategy_capsule(capsule_id)
        for capsule_id in sorted(capsule_ids[:_MAX_CAPSULES])
    )
    if any(item is None for item in capsules):
        raise ValueError
    shadow = KrDayCapsuleShadowStore(config.state_root / "kr-day-capsule-shadow.sqlite3")
    paths = tuple(
        path
        for stored in capsules
        if stored is not None
        for path in _materialize_capsule(
            config,
            evaluated_at,
            stored,
            opportunities,
            calendars,
            cycles,
            shadow.latest(stored.capsule.capsule_id, local_date.isoformat()),
        )
    )
    return paths


def _materialize_capsule(
    config: KrDaySessionServiceConfig,
    evaluated_at: dt.datetime,
    stored: StoredDayStrategyCapsule,
    opportunities: tuple[OpportunitySnapshot, ...],
    calendars: tuple[KrSessionCalendarSnapshot, ...],
    cycles: tuple[Path, ...],
    latest: KrDayCapsuleShadowEvent | None,
) -> tuple[Path, ...]:
    selection = _select_source(evaluated_at, opportunities, calendars, cycles, latest)
    if selection is None:
        return ()
    symbol = selection.opportunity.candidates[0].symbol
    receipts = tuple(
        item
        for item in KisKrMarketReceiptStore(selection.cycle / f"{symbol}.market.sqlite3").receipts()
        if item.symbol == symbol and item.received_at <= evaluated_at
    )
    minute_receipts = tuple(item for item in receipts if item.kind is KisKrMarketReceiptKind.MINUTE_BARS)
    prices = tuple(item for item in receipts if item.kind is KisKrMarketReceiptKind.PRICE_STATUS)
    quotes = tuple(item for item in receipts if item.kind is KisKrMarketReceiptKind.ORDER_BOOK)
    if not minute_receipts or not prices or not quotes:
        raise ValueError
    bars = project_kis_kr_recent_completed_minutes(
        KisKrMinuteProjectionInput(receipts=minute_receipts, evaluated_at=evaluated_at)
    )
    if evaluated_at - bars[-1].observed_at > dt.timedelta(seconds=30):
        return ()
    market = project_kis_kr_market_snapshot(
        KisKrSnapshotProjectionInput(
            price_receipt=prices[-1],
            quote_receipt=quotes[-1],
            evaluated_at=evaluated_at,
        )
    )
    request = KrDayCapsuleEvaluationRequest(
        capsule=stored.capsule,
        calendar=selection.calendar,
        opportunity=selection.opportunity,
        market=market,
        bars=bars,
        evaluated_at=evaluated_at,
        max_slippage_bps=Decimal("20"),
    )
    canonical = canonical_experiment_ledger_json(request)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    local_date = evaluated_at.astimezone(_KST).date()
    bar = request.bars[-1].end_at.astimezone(_KST).strftime("%H%M%S")
    path = (
        config.state_root
        / "materialized_requests"
        / local_date.isoformat()
        / f"{stored.capsule.capsule_id}-{bar}-{digest}.json"
    )
    _ = publish_private_immutable_text(path, canonical + "\n")
    return (path,)


def _select_source(
    evaluated_at: dt.datetime,
    opportunities: tuple[OpportunitySnapshot, ...],
    calendars: tuple[KrSessionCalendarSnapshot, ...],
    cycles: tuple[Path, ...],
    latest: KrDayCapsuleShadowEvent | None,
) -> _SourceSelection | None:
    if latest is not None and latest.status is KrDayCapsuleShadowStatus.ACTIVE:
        opportunity = next(
            (
                item
                for item in opportunities
                if item.candidates[0].symbol == latest.symbol
                and _cycle_id(item) == latest.collection_cycle_id
            ),
            None,
        )
        calendar = next(
            (item for item in calendars if item.snapshot_id == latest.calendar_snapshot_id),
            None,
        )
        cycle = next((item for item in cycles if item.name == latest.collection_cycle_id), None)
        if opportunity is None or calendar is None or cycle is None:
            raise ValueError
        return _SourceSelection(opportunity, calendar, cycle)
    opportunity = next((item for item in opportunities if evaluated_at < item.valid_until), None)
    if opportunity is None:
        return None
    cycle_id = _cycle_id(opportunity)
    cycle = next((item for item in cycles if item.name == cycle_id), None)
    if cycle is None:
        raise ValueError
    return _SourceSelection(opportunity, calendars[-1], cycle)


def _cycle_id(opportunity: OpportunitySnapshot) -> str:
    cycle_ids = tuple(
        item.record_id
        for item in opportunity.evidence_refs
        if item.namespace == "kr/collection_cycle"
    )
    if len(cycle_ids) != 1:
        raise ValueError
    return cycle_ids[0]


__all__ = ("materialize_kr_requests",)
