from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import pairwise
from typing import Final

from pydantic import ValidationError

from trading_agent.kis_kr_market_models import (
    KisKrMarketEvidenceError,
    KisKrMarketReceipt,
    KisKrMinuteProjectionInput,
    KisKrMinuteRow,
    KisKrPriceStatusRow,
    KisKrSnapshotProjectionInput,
)
from trading_agent.kis_kr_market_parsing import (
    SEOUL,
    decimal_value,
    optional_price,
    parse_bar_start,
    parse_minute_envelope,
    parse_price_envelope,
    parse_quote_envelope,
    parse_quote_time,
    positive_int,
)
from trading_agent.kr_intraday_market_gate import (
    KrDesignationState,
    KrHaltState,
    KrMarketConstraintSnapshot,
    KrSessionState,
    KrTradingMode,
    KrViState,
)
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.signal_contract_models import EvidenceRef

_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_MAX_RECEIPT_SKEW: Final = dt.timedelta(seconds=2)
_MAX_QUOTE_DELAY: Final = dt.timedelta(seconds=5)
_SESSION_OPEN: Final = dt.time(9)


def project_kis_kr_completed_minutes(
    source: KisKrMinuteProjectionInput,
) -> tuple[KrCompletedMinuteBar, ...]:
    request = _validated_minute_input(source)
    observed_rows: dict[dt.datetime, tuple[KisKrMinuteRow, KisKrMarketReceipt]] = {}
    for receipt in sorted(request.receipts, key=lambda item: item.received_at):
        envelope = parse_minute_envelope(receipt)
        for row in envelope.output2:
            started_at = parse_bar_start(row)
            if started_at + _ONE_MINUTE > receipt.received_at:
                continue
            existing = observed_rows.get(started_at)
            if existing is not None and existing[0] != row:
                raise KisKrMarketEvidenceError
            if existing is None:
                observed_rows[started_at] = (row, receipt)
    ordered = tuple(sorted(observed_rows.items()))
    if not ordered or ordered[0][0].astimezone(SEOUL).time() != _SESSION_OPEN:
        raise KisKrMarketEvidenceError
    if any(current[0] - previous[0] != _ONE_MINUTE for previous, current in pairwise(ordered)):
        raise KisKrMarketEvidenceError
    return _completed_bars(ordered)


def project_kis_kr_market_snapshot(
    source: KisKrSnapshotProjectionInput,
) -> KrMarketConstraintSnapshot:
    request = _validated_snapshot_input(source)
    receipts = (request.price_receipt, request.quote_receipt)
    if max(item.received_at for item in receipts) - min(item.received_at for item in receipts) > _MAX_RECEIPT_SKEW:
        raise KisKrMarketEvidenceError
    price_envelope = parse_price_envelope(request.price_receipt)
    quote_envelope = parse_quote_envelope(request.quote_receipt)
    price = price_envelope.output
    quote = quote_envelope.output1
    status = quote_envelope.output2
    if (
        price.stck_shrn_iscd != request.price_receipt.symbol
        or status.stck_shrn_iscd != request.quote_receipt.symbol
        or price.stck_shrn_iscd != status.stck_shrn_iscd
        or price.stck_prpr != status.stck_prpr
        or price.stck_sdpr != status.stck_sdpr
        or price.vi_cls_code != status.vi_cls_code
    ):
        raise KisKrMarketEvidenceError
    quote_at = parse_quote_time(request.quote_receipt, quote.aspr_acpt_hour)
    quote_delay = request.quote_receipt.received_at - quote_at
    if quote_delay < dt.timedelta(0) or quote_delay > _MAX_QUOTE_DELAY:
        raise KisKrMarketEvidenceError
    session_state, trading_mode = _market_mode(status.new_mkop_cls_code)
    evidence = tuple(
        sorted(
            (
                _evidence(request.price_receipt, "status/kis-kr-rest"),
                _evidence(request.quote_receipt, "quote/kis-kr-rest"),
            ),
            key=lambda item: item.canonical_id,
        )
    )
    try:
        return KrMarketConstraintSnapshot(
            symbol=price.stck_shrn_iscd,
            observed_at=max(item.received_at for item in receipts),
            previous_close=decimal_value(price.stck_sdpr),
            last_price=decimal_value(price.stck_prpr),
            bid_price=optional_price(quote.bidp1),
            ask_price=optional_price(quote.askp1),
            lower_limit_price=decimal_value(price.stck_llam),
            upper_limit_price=decimal_value(price.stck_mxpr),
            session_state=session_state,
            vi_state=KrViState.CLEAR if price.vi_cls_code == "N" else KrViState.UNKNOWN,
            trading_mode=trading_mode,
            halt_state=_halt_state(price.temp_stop_yn),
            designation_state=_designation_state(price),
            evidence_refs=evidence,
        )
    except (ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None


def _completed_bars(
    ordered: tuple[tuple[dt.datetime, tuple[KisKrMinuteRow, KisKrMarketReceipt]], ...],
) -> tuple[KrCompletedMinuteBar, ...]:
    bars: list[KrCompletedMinuteBar] = []
    prior_cumulative = Decimal(0)
    try:
        for started_at, (row, receipt) in ordered:
            cumulative = decimal_value(row.acml_tr_pbmn)
            trading_value = cumulative - prior_cumulative
            prior_cumulative = cumulative
            bars.append(
                KrCompletedMinuteBar(
                    symbol=receipt.symbol,
                    start_at=started_at,
                    end_at=started_at + _ONE_MINUTE,
                    observed_at=receipt.received_at,
                    open=decimal_value(row.stck_oprc),
                    high=decimal_value(row.stck_hgpr),
                    low=decimal_value(row.stck_lwpr),
                    close=decimal_value(row.stck_prpr),
                    volume=positive_int(row.cntg_vol),
                    trading_value_krw=trading_value,
                    evidence_ref=_bar_evidence(receipt, started_at),
                )
            )
    except (ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None
    return tuple(bars)


def _validated_minute_input(source: KisKrMinuteProjectionInput) -> KisKrMinuteProjectionInput:
    try:
        return KisKrMinuteProjectionInput.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None


def _validated_snapshot_input(source: KisKrSnapshotProjectionInput) -> KisKrSnapshotProjectionInput:
    try:
        return KisKrSnapshotProjectionInput.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None


def _market_mode(code: str) -> tuple[KrSessionState, KrTradingMode]:
    if code == "20":
        return KrSessionState.OPEN, KrTradingMode.CONTINUOUS
    return KrSessionState.UNKNOWN, KrTradingMode.UNKNOWN


def _halt_state(code: str) -> KrHaltState:
    if code == "N":
        return KrHaltState.CLEAR
    if code == "Y":
        return KrHaltState.HALTED
    return KrHaltState.UNKNOWN


def _designation_state(price: KisKrPriceStatusRow) -> KrDesignationState:
    flags = (price.invt_caful_yn, price.short_over_yn, price.sltr_yn)
    if (
        any(value not in {"N", "Y"} for value in flags)
        or price.mrkt_warn_cls_code not in {"00", "01", "02", "03"}
        or price.mang_issu_cls_code not in {"00", "01", "N", "Y"}
    ):
        return KrDesignationState.UNKNOWN
    if "Y" in flags or price.mrkt_warn_cls_code != "00" or price.mang_issu_cls_code in {"01", "Y"}:
        return KrDesignationState.WARNING
    return KrDesignationState.CLEAR


def _bar_evidence(receipt: KisKrMarketReceipt, started_at: dt.datetime) -> EvidenceRef:
    return EvidenceRef(
        namespace="bar/kis-kr-rest",
        record_id=f"{receipt.payload_sha256}:{started_at.strftime('%Y%m%d%H%M%S')}",
        observed_at=receipt.received_at,
    )


def _evidence(receipt: KisKrMarketReceipt, namespace: str) -> EvidenceRef:
    return EvidenceRef(
        namespace=namespace,
        record_id=receipt.payload_sha256,
        observed_at=receipt.received_at,
    )
