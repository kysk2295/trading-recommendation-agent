from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Final, Self

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scr_backtest.kis_intraday import MissingKisCredentialsError
from trading_agent.kis_auth import (
    DEFAULT_TOKEN_DIR,
    InvalidKisTokenCacheError,
    KisMode,
    UnsafeSecretFileError,
    create_kis_client,
    load_cached_kis_access_token,
    load_kis_credentials,
)
from trading_agent.kis_kr_market_client import KisKrMarketClient, KisKrMarketTransportError
from trading_agent.kis_kr_market_collection import (
    KisKrMarketCollectionPhase,
    KisKrMarketCollectionRequest,
    collect_kis_kr_market_receipts,
)
from trading_agent.kis_kr_market_models import (
    KisKrMarketEvidenceError,
    KisKrMarketReceipt,
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
from trading_agent.kr_autonomous_market_models import (
    KrAutonomousMarketCorroboration,
    KrAutonomousMarketError,
    KrAutonomousMarketErrorReason,
    corroboration_id,
)
from trading_agent.kr_intraday_market_gate import KrIntradayGateStatus, assess_kr_shadow_entry
from trading_agent.kr_session_runtime_gate import require_open_kr_runtime_session
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig

_KIS_TOKEN_CACHE_DIR: Final = DEFAULT_TOKEN_DIR


class KrCorroborationProjectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    signal: KrSocialSignal
    calendar_snapshot: KrSessionCalendarSnapshot
    receipts: tuple[KisKrMarketReceipt, ...]
    observed_at: dt.datetime

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not _aware(self.observed_at):
            raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.INVALID_INPUT)
        return self


def collect_and_project_kr_corroboration(
    signal: KrSocialSignal,
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> KrAutonomousMarketCorroboration:
    trusted_signal = _validated_signal(signal, now)
    try:
        trusted_config = ResearchAgentServiceConfig.model_validate(config.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.INVALID_INPUT) from None
    if (
        trusted_config.schema_version != 4
        or trusted_config.source_paths.kr_calendar_store is None
        or trusted_config.kr_market_receipt_root is None
    ):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.INVALID_INPUT)
    snapshot = _current_calendar(trusted_config.source_paths.kr_calendar_store, now)
    try:
        credentials = load_kis_credentials(KisMode.LIVE)
        access_token = load_cached_kis_access_token(
            KisMode.LIVE,
            cache_dir=_KIS_TOKEN_CACHE_DIR,
            now=now,
        )
    except (
        InvalidKisTokenCacheError,
        MissingKisCredentialsError,
        OSError,
        UnsafeSecretFileError,
    ):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.CREDENTIAL_BOUNDARY_FAILED) from None
    try:
        with create_kis_client(KisMode.LIVE) as http_client:
            market_client = KisKrMarketClient(http_client, credentials, access_token, _clock=lambda: now)
            store = KisKrMarketReceiptStore(trusted_config.kr_market_receipt_root / f"{trusted_signal.symbol}.sqlite3")
            _ = collect_kis_kr_market_receipts(
                market_client,
                store,
                KisKrMarketCollectionRequest(
                    symbol=trusted_signal.symbol,
                    session_date=now.astimezone(dt.timezone(dt.timedelta(hours=9))).date(),
                    clock=lambda: now,
                    phase=KisKrMarketCollectionPhase.INTRADAY,
                ),
            )
            receipts = store.receipts()
    except (
        httpx2.HTTPError,
        KisKrMarketTransportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.MARKET_EVIDENCE_INVALID) from None
    return project_kr_corroboration(
        KrCorroborationProjectionInput(
            signal=trusted_signal,
            calendar_snapshot=snapshot,
            receipts=receipts,
            observed_at=now,
        )
    )


def project_kr_corroboration(
    source: KrCorroborationProjectionInput,
) -> KrAutonomousMarketCorroboration:
    try:
        request = KrCorroborationProjectionInput(
            signal=source.signal,
            calendar_snapshot=source.calendar_snapshot,
            receipts=source.receipts,
            observed_at=source.observed_at,
        )
        signal = _validated_signal(request.signal, request.observed_at)
        session_date = require_open_kr_runtime_session(request.calendar_snapshot, request.observed_at)
        receipts = _current_receipts(request.receipts, signal.symbol, request.observed_at)
        response_at = max(item.received_at for item in receipts)
        if response_at < signal.first_observed_at:
            raise KisKrMarketEvidenceError
        minute = _receipt(receipts, KisKrMarketReceiptKind.MINUTE_BARS)
        price = _receipt(receipts, KisKrMarketReceiptKind.PRICE_STATUS)
        quote = _receipt(receipts, KisKrMarketReceiptKind.ORDER_BOOK)
        bars = project_kis_kr_recent_completed_minutes(
            KisKrMinuteProjectionInput(receipts=(minute,), evaluated_at=request.observed_at)
        )
        market = project_kis_kr_market_snapshot(
            KisKrSnapshotProjectionInput(
                price_receipt=price,
                quote_receipt=quote,
                evaluated_at=request.observed_at,
            )
        )
        latest = bars[-1]
        gate = assess_kr_shadow_entry(market, request.observed_at)
        if gate.status is not KrIntradayGateStatus.ELIGIBLE:
            raise KisKrMarketEvidenceError
        if market.bid_price is None or market.ask_price is None:
            raise KisKrMarketEvidenceError
        midpoint = (market.bid_price + market.ask_price) / Decimal(2)
        spread_bps = (market.ask_price - market.bid_price) / midpoint * Decimal(10_000)
        result = KrAutonomousMarketCorroboration.model_construct(
            corroboration_id="",
            task_id=signal.task_id,
            social_signal_id=signal.signal_id,
            symbol=signal.symbol,
            session_date=session_date,
            calendar_snapshot_id=request.calendar_snapshot.snapshot_id,
            social_first_observed_at=signal.first_observed_at,
            market_response_at=response_at,
            observed_at=request.observed_at,
            valid_until=market.observed_at + dt.timedelta(seconds=5),
            latest_completed_bar=latest,
            market_snapshot=market,
            spread_bps=spread_bps,
            trading_value_krw=latest.trading_value_krw,
            receipt_count=len(receipts),
            receipt_sha256s=tuple(sorted(item.payload_sha256 for item in receipts)),
            evidence_count=len(market.evidence_refs) + 1,
            evidence_ids=tuple(
                sorted((*map(lambda item: item.canonical_id, market.evidence_refs), latest.evidence_ref.canonical_id))
            ),
        )
        return KrAutonomousMarketCorroboration.model_validate(
            result.model_copy(update={"corroboration_id": corroboration_id(result)}).model_dump(mode="python")
        )
    except (AttributeError, IndexError, TypeError, ValidationError, ValueError):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.MARKET_EVIDENCE_INVALID) from None


def _validated_signal(signal: KrSocialSignal, now: dt.datetime) -> KrSocialSignal:
    try:
        trusted = KrSocialSignal.model_validate(signal.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.INVALID_INPUT) from None
    if not _aware(now) or trusted.normalized_at > now:
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.INVALID_INPUT)
    return trusted


def _current_calendar(path: Path, now: dt.datetime) -> KrSessionCalendarSnapshot:
    try:
        current_date = now.astimezone(dt.timezone(dt.timedelta(hours=9))).date()
        matches = tuple(
            item for item in KisKrSessionCalendarStore(path).snapshots() if item.payload.base_date == current_date
        )
    except (OSError, TypeError, ValueError):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.CALENDAR_UNAVAILABLE) from None
    if len(matches) != 1:
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.CALENDAR_UNAVAILABLE)
    try:
        _ = require_open_kr_runtime_session(matches[0], now)
        return matches[0]
    except (OSError, TypeError, ValueError):
        raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.SESSION_UNAVAILABLE) from None


def _current_receipts(
    receipts: tuple[KisKrMarketReceipt, ...], symbol: str, now: dt.datetime
) -> tuple[KisKrMarketReceipt, ...]:
    current_date = now.astimezone(dt.timezone(dt.timedelta(hours=9))).date()
    selected: list[KisKrMarketReceipt] = []
    for kind in KisKrMarketReceiptKind:
        matches = tuple(
            item
            for item in receipts
            if item.kind is kind
            and item.symbol == symbol
            and item.received_at <= now
            and item.received_at.astimezone(dt.timezone(dt.timedelta(hours=9))).date() == current_date
        )
        if not matches:
            raise KisKrMarketEvidenceError
        selected.append(max(matches, key=lambda item: item.received_at))
    return tuple(selected)


def _receipt(receipts: tuple[KisKrMarketReceipt, ...], kind: KisKrMarketReceiptKind) -> KisKrMarketReceipt:
    return next(item for item in receipts if item.kind is kind)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "KrCorroborationProjectionInput",
    "collect_and_project_kr_corroboration",
    "project_kr_corroboration",
)
