from __future__ import annotations

import datetime as dt
from typing import override

from pydantic import ValidationError

from trading_agent.kis_kr_session_calendar_models import (
    KIS_CALENDAR_ADAPTER_VERSION,
    KIS_CALENDAR_SOURCE_COMMIT,
    KisKrSessionCalendarEnvelope,
    KisKrSessionCalendarReceipt,
    KisKrSessionCalendarRow,
    KrSessionCalendarPayload,
    KrSessionCalendarSnapshot,
    KrSessionDay,
    kr_session_calendar_snapshot,
)


class InvalidKisKrSessionCalendarError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR session calendar projection is invalid"


def project_kis_kr_session_calendar(
    receipt: KisKrSessionCalendarReceipt,
) -> KrSessionCalendarSnapshot:
    try:
        if receipt.status_code != 200 or receipt.content_type != "application/json":
            raise InvalidKisKrSessionCalendarError
        envelope = KisKrSessionCalendarEnvelope.model_validate_json(receipt.raw_payload)
        if envelope.rt_cd != "0" or not envelope.output:
            raise InvalidKisKrSessionCalendarError
        days = tuple(_day(row) for row in envelope.output)
        payload = KrSessionCalendarPayload(
            source_commit=KIS_CALENDAR_SOURCE_COMMIT,
            adapter_version=KIS_CALENDAR_ADAPTER_VERSION,
            base_date=receipt.base_date,
            observed_at=receipt.received_at,
            receipt_sha256=receipt.payload_sha256,
            days=days,
        )
        return kr_session_calendar_snapshot(payload)
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKisKrSessionCalendarError from None


def next_kr_open_session(
    snapshot: KrSessionCalendarSnapshot,
    after_session: dt.date,
) -> dt.date:
    try:
        snapshot = KrSessionCalendarSnapshot.model_validate(snapshot.model_dump(mode="python"))
        if after_session < snapshot.payload.base_date:
            raise InvalidKisKrSessionCalendarError
        matches = tuple(
            day.session_date
            for day in snapshot.payload.days
            if day.session_date > after_session and day.business_day and day.trading_day and day.open_day
        )
        if not matches:
            raise InvalidKisKrSessionCalendarError
        return matches[0]
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKisKrSessionCalendarError from None


def _day(row: KisKrSessionCalendarRow) -> KrSessionDay:
    try:
        session_date = dt.datetime.strptime(row.bass_dt, "%Y%m%d").date()
        if session_date.strftime("%Y%m%d") != row.bass_dt:
            raise InvalidKisKrSessionCalendarError
        return KrSessionDay(
            session_date=session_date,
            weekday_code=row.wday_dvsn_cd,
            business_day=_yn(row.bzdy_yn),
            trading_day=_yn(row.tr_day_yn),
            open_day=_yn(row.opnd_yn),
            settlement_day=_yn(row.sttl_day_yn),
        )
    except (AttributeError, TypeError, ValueError):
        raise InvalidKisKrSessionCalendarError from None


def _yn(value: str) -> bool:
    if value not in {"N", "Y"}:
        raise InvalidKisKrSessionCalendarError
    return value == "Y"
