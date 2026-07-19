from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kis_kr_market_models import (
    KisKrMarketReceiptKind,
    KisKrMinuteProjectionInput,
)
from trading_agent.kis_kr_market_projection import project_kis_kr_completed_minutes
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit import project_kr_theme_day_shadow_exit
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore

KST = ZoneInfo("Asia/Seoul")


class InvalidKrThemeDayShadowExitCycleError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow exit cycle is invalid"


class KrThemeDayShadowExitCycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    evaluated_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if not self.trial_id or not _aware(self.evaluated_at):
            raise InvalidKrThemeDayShadowExitCycleError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayShadowExitStores:
    receipts: KisKrMarketReceiptStore
    entries: KrThemeDayShadowEntryStore
    exits: KrThemeDayShadowExitStore


@dataclass(frozen=True, slots=True)
class KrThemeDayShadowExitCycleResult:
    terminal_entry_count: int
    open_entry_count: int
    pending_entry_count: int
    created_exit_count: int


def run_kr_theme_day_shadow_exit_cycle(
    stores: KrThemeDayShadowExitStores,
    source: KrThemeDayShadowExitCycleRequest,
) -> KrThemeDayShadowExitCycleResult:
    try:
        request = KrThemeDayShadowExitCycleRequest.model_validate(source.model_dump(mode="python"))
        entries = tuple(entry for entry in stores.entries.entries() if entry.trial_id == request.trial_id)
        exits = tuple(exit for exit in stores.exits.exits() if exit.trial_id == request.trial_id)
        entry_ids = {entry.entry_id for entry in entries}
        terminal_ids = {exit.entry_id for exit in exits}
        if len(entry_ids) != len(entries) or len(terminal_ids) != len(exits) or not terminal_ids <= entry_ids:
            raise InvalidKrThemeDayShadowExitCycleError
        open_entries = tuple(entry for entry in entries if entry.entry_id not in terminal_ids)
        created = 0
        pending = 0
        for entry in open_entries:
            bars = _entry_bars(stores.receipts, entry, request.evaluated_at)
            if not bars:
                pending += 1
                continue
            result = project_kr_theme_day_shadow_exit(
                stores.entries,
                stores.exits,
                entry.entry_id,
                bars,
                evaluated_at=request.evaluated_at,
            )
            if result is None:
                pending += 1
            else:
                created += int(result.created)
    except (AttributeError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayShadowExitCycleError from None
    return KrThemeDayShadowExitCycleResult(len(exits), len(open_entries), pending, created)


def _entry_bars(
    store: KisKrMarketReceiptStore,
    entry: KrThemeDayShadowEntry,
    evaluated_at: dt.datetime,
) -> tuple[KrCompletedMinuteBar, ...]:
    session = entry.filled_at.astimezone(KST).date()
    receipts = tuple(
        receipt
        for receipt in store.receipts()
        if receipt.kind is KisKrMarketReceiptKind.MINUTE_BARS
        and receipt.symbol == entry.symbol
        and receipt.received_at <= evaluated_at
        and receipt.received_at.astimezone(KST).date() == session
    )
    if not receipts:
        return ()
    bars = project_kis_kr_completed_minutes(KisKrMinuteProjectionInput(receipts=receipts, evaluated_at=evaluated_at))
    first_start = _first_full_start(entry.filled_at)
    return tuple(bar for bar in bars if bar.start_at.astimezone(KST) >= first_start)


def _first_full_start(filled_at: dt.datetime) -> dt.datetime:
    local = filled_at.astimezone(KST)
    floor = local.replace(second=0, microsecond=0)
    return floor if local == floor else floor + dt.timedelta(minutes=1)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
