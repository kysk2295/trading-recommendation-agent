from __future__ import annotations

import datetime as dt
import json
import sqlite3
import stat
from pathlib import Path

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_kr_session_calendar import (
    InvalidKisKrSessionCalendarError,
    next_kr_open_session,
    project_kis_kr_session_calendar,
)
from trading_agent.kis_kr_session_calendar_client import (
    KIS_KR_CALENDAR_BASE_URL,
    KisKrSessionCalendarClient,
    KisKrSessionCalendarFetchRequest,
    KisKrSessionCalendarTransportError,
    UnsafeKisKrSessionCalendarEndpointError,
)
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)

KST = dt.timezone(dt.timedelta(hours=9))
REQUESTED_AT = dt.datetime(2026, 7, 20, 15, 31, tzinfo=KST)
TOKEN = "dummy-token"


def test_client_uses_only_official_read_only_holiday_contract() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=_payload(),
        )

    with httpx2.Client(
        base_url=KIS_KR_CALENDAR_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisKrSessionCalendarClient(
            http_client,
            _credentials(),
            TOKEN,
            _clock=lambda: REQUESTED_AT,
        )
        receipt = client.fetch(_request())

    assert receipt.base_date == dt.date(2026, 7, 20)
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/uapi/domestic-stock/v1/quotations/chk-holiday"
    assert seen[0].headers["tr_id"] == "CTCA0903R"
    assert dict(seen[0].url.params) == {
        "BASS_DT": "20260720",
        "CTX_AREA_FK": "",
        "CTX_AREA_NK": "",
    }
    assert not {"CANO", "ACNT_PRDT_CD", "ORD_QTY", "ORD_UNPR"} & set(seen[0].url.params)


def test_projection_skips_holiday_and_store_replays_private_raw_receipt(tmp_path: Path) -> None:
    receipt = _receipt()
    snapshot = project_kis_kr_session_calendar(receipt)
    store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")

    assert next_kr_open_session(snapshot, dt.date(2026, 7, 20)) == dt.date(2026, 7, 22)
    assert store.append(receipt, snapshot) is True
    assert store.append(receipt, snapshot) is False
    assert store.snapshots() == (snapshot,)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_projection_rejects_inconsistent_flags_or_missing_next_open() -> None:
    inconsistent = _receipt(
        rows=(
            _row("20260720", "Y", "Y", "Y", "Y"),
            _row("20260721", "Y", "N", "Y", "Y"),
        )
    )
    missing = _receipt(rows=(_row("20260720", "Y", "Y", "Y", "Y"),))

    with pytest.raises(InvalidKisKrSessionCalendarError):
        _ = project_kis_kr_session_calendar(inconsistent)
    with pytest.raises(InvalidKisKrSessionCalendarError):
        _ = next_kr_open_session(project_kis_kr_session_calendar(missing), dt.date(2026, 7, 20))


def test_store_detects_raw_or_schema_tamper(tmp_path: Path) -> None:
    receipt = _receipt()
    snapshot = project_kis_kr_session_calendar(receipt)
    store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    assert store.append(receipt, snapshot) is True
    with sqlite3.connect(store.path) as connection:
        _ = connection.execute("DROP TRIGGER kis_kr_session_calendars_no_update")
        _ = connection.execute("UPDATE kis_kr_session_calendars SET raw_payload = '{}' ")
        connection.commit()

    with pytest.raises(InvalidKisKrSessionCalendarStoreError):
        _ = store.snapshots()


def test_client_rejects_wrong_origin_and_redacts_transport_error() -> None:
    with (
        httpx2.Client(base_url="https://example.com", follow_redirects=False) as wrong,
        pytest.raises(UnsafeKisKrSessionCalendarEndpointError),
    ):
        _ = KisKrSessionCalendarClient(wrong, _credentials(), TOKEN)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadError("PRIVATE calendar credential detail", request=request)

    with httpx2.Client(
        base_url=KIS_KR_CALENDAR_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisKrSessionCalendarClient(
            http_client,
            _credentials(),
            TOKEN,
            _clock=lambda: REQUESTED_AT,
        )
        with pytest.raises(KisKrSessionCalendarTransportError) as captured:
            _ = client.fetch(_request())
    assert str(captured.value) == "KIS KR session calendar read-only transport failed"
    assert "PRIVATE" not in str(captured.value)


def _request() -> KisKrSessionCalendarFetchRequest:
    return KisKrSessionCalendarFetchRequest(
        base_date=dt.date(2026, 7, 20),
        requested_at=REQUESTED_AT,
    )


def _receipt(*, rows: tuple[dict[str, str], ...] | None = None) -> KisKrSessionCalendarReceipt:
    return KisKrSessionCalendarReceipt(
        base_date=dt.date(2026, 7, 20),
        received_at=REQUESTED_AT,
        status_code=200,
        content_type="application/json",
        raw_payload=_payload(rows=rows),
    )


def _payload(*, rows: tuple[dict[str, str], ...] | None = None) -> bytes:
    output = rows or (
        _row("20260720", "Y", "Y", "Y", "Y"),
        _row("20260721", "N", "N", "N", "N"),
        _row("20260722", "Y", "Y", "Y", "Y"),
    )
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "success",
            "ctx_area_fk": "",
            "ctx_area_nk": "",
            "output": output,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _row(date: str, business: str, trading: str, open_day: str, settlement: str) -> dict[str, str]:
    return {
        "bass_dt": date,
        "wday_dvsn_cd": "1",
        "bzdy_yn": business,
        "tr_day_yn": trading,
        "opnd_yn": open_day,
        "sttl_day_yn": settlement,
    }


def _credentials() -> KisCredentials:
    return KisCredentials(app_key="dummy-app-key", app_secret="dummy-app-secret")
