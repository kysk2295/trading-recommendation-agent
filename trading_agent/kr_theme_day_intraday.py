from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
    KisKrMinuteProjectionInput,
    KisKrSnapshotProjectionInput,
)
from trading_agent.kis_kr_market_projection import (
    project_kis_kr_completed_minutes,
    project_kis_kr_market_snapshot,
)
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_setup import (
    KrThemeDaySetupInput,
    derive_kr_theme_day_setup,
)
from trading_agent.kr_theme_day_shadow_entry import project_kr_theme_day_shadow_entry
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_signal import project_kr_theme_day_shadow_signal
from trading_agent.signal_contract_models import OpportunitySnapshot, TradeSignalEnvelope

KST = ZoneInfo("Asia/Seoul")


class InvalidKrThemeDayIntradayError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day intraday input is invalid"


class KrThemeDayIntradayStatus(StrEnum):
    NO_SETUP = "no_setup"
    MARKET_BLOCKED = "market_blocked"
    ENTRY_CREATED = "entry_created"
    ENTRY_REPLAYED = "entry_replayed"


class KrThemeDayIntradayEntryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity: OpportunitySnapshot
    producer_strategy_version: str
    evaluated_at: dt.datetime
    filled_at: dt.datetime
    max_slippage_bps: Decimal

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not self.producer_strategy_version
            or not _aware(self.evaluated_at)
            or not _aware(self.filled_at)
            or self.filled_at < self.evaluated_at
            or not self.max_slippage_bps.is_finite()
            or self.max_slippage_bps <= 0
        ):
            raise InvalidKrThemeDayIntradayError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayIntradayOutcome:
    status: KrThemeDayIntradayStatus
    signal: TradeSignalEnvelope | None
    entry: KrThemeDayShadowEntry | None


def run_kr_theme_day_intraday_entry(
    ledger: ExperimentLedgerStore,
    receipt_store: KisKrMarketReceiptStore,
    entry_store: KrThemeDayShadowEntryStore,
    source: KrThemeDayIntradayEntryRequest,
) -> KrThemeDayIntradayOutcome:
    try:
        request = KrThemeDayIntradayEntryRequest.model_validate(source.model_dump(mode="python"))
        minutes, price, quote = _evidence(receipt_store, request)
        bars = project_kis_kr_completed_minutes(
            KisKrMinuteProjectionInput(receipts=minutes, evaluated_at=request.evaluated_at)
        )
        setup = derive_kr_theme_day_setup(
            KrThemeDaySetupInput(
                opportunity=request.opportunity,
                bars=bars,
                producer_strategy_version=request.producer_strategy_version,
                evaluated_at=request.evaluated_at,
                max_slippage_bps=request.max_slippage_bps,
            )
        )
        if setup is None:
            return KrThemeDayIntradayOutcome(KrThemeDayIntradayStatus.NO_SETUP, None, None)
        market = project_kis_kr_market_snapshot(
            KisKrSnapshotProjectionInput(
                price_receipt=price,
                quote_receipt=quote,
                evaluated_at=request.evaluated_at,
            )
        )
        decision = project_kr_theme_day_shadow_signal(
            request.opportunity,
            market,
            setup,
            evaluated_at=request.evaluated_at,
        )
        if decision.signal is None:
            return KrThemeDayIntradayOutcome(KrThemeDayIntradayStatus.MARKET_BLOCKED, None, None)
        result = project_kr_theme_day_shadow_entry(
            ledger,
            entry_store,
            decision.signal,
            filled_at=request.filled_at,
        )
    except (AttributeError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayIntradayError from None
    status = KrThemeDayIntradayStatus.ENTRY_CREATED if result.created else KrThemeDayIntradayStatus.ENTRY_REPLAYED
    return KrThemeDayIntradayOutcome(status, decision.signal, result.entry)


def _evidence(
    store: KisKrMarketReceiptStore,
    request: KrThemeDayIntradayEntryRequest,
) -> tuple[tuple[KisKrMarketReceipt, ...], KisKrMarketReceipt, KisKrMarketReceipt]:
    symbol = request.opportunity.candidates[0].symbol
    session = request.evaluated_at.astimezone(KST).date()
    eligible = tuple(
        receipt
        for receipt in store.receipts()
        if receipt.symbol == symbol
        and receipt.received_at <= request.evaluated_at
        and receipt.received_at.astimezone(KST).date() == session
    )
    minutes = tuple(receipt for receipt in eligible if receipt.kind is KisKrMarketReceiptKind.MINUTE_BARS)
    prices = tuple(receipt for receipt in eligible if receipt.kind is KisKrMarketReceiptKind.PRICE_STATUS)
    quotes = tuple(receipt for receipt in eligible if receipt.kind is KisKrMarketReceiptKind.ORDER_BOOK)
    if not minutes or not prices or not quotes:
        raise InvalidKrThemeDayIntradayError
    return minutes, prices[-1], quotes[-1]


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
