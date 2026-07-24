from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent.kis_futures_entitlement_admission import (
    KisFuturesAdmissionReason,
    KisFuturesAdmissionStatus,
    evaluate_kis_futures_entitlement_admission,
    publish_kis_futures_entitlement_admission,
)
from trading_agent.kis_overseas_futures_collection import (
    collect_kis_overseas_futures_quotes,
)
from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
)
from trading_agent.kis_overseas_futures_store import KisOverseasFuturesStore

OBSERVED_AT = dt.datetime(2026, 7, 24, 4, 45, tzinfo=dt.UTC)


class _Fetcher:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self.payload = payload

    def fetch(
        self,
        request: KisFuturesQuoteRequest,
        symbol: str,
    ) -> KisFuturesQuoteRawResponse:
        return KisFuturesQuoteRawResponse(
            request_id=request.request_id,
            symbol=symbol,
            received_at=OBSERVED_AT,
            status_code=self.status,
            content_type="application/json",
            raw_payload=self.payload,
        )


def test_successful_terminal_is_ready_and_artifact_replays(tmp_path: Path) -> None:
    request = _request()
    store = KisOverseasFuturesStore(tmp_path / "quotes.sqlite3")
    _ = collect_kis_overseas_futures_quotes(
        _Fetcher(200, _success_payload()),
        store,
        request,
        _clock=lambda: OBSERVED_AT,
    )

    admission = evaluate_kis_futures_entitlement_admission(store, request)
    first = publish_kis_futures_entitlement_admission(
        tmp_path / "admission",
        admission,
    )
    replay = publish_kis_futures_entitlement_admission(
        tmp_path / "admission",
        admission,
    )

    assert admission.status is KisFuturesAdmissionStatus.READY
    assert admission.reason is KisFuturesAdmissionReason.BOUNDED_QUOTES_COMPLETE
    assert admission.canonical_quote_count == 2
    assert first[1]
    assert not replay[1]
    assert first[0] == replay[0]


def test_actual_provider_code_is_machine_readable_blocked(tmp_path: Path) -> None:
    request = _request()
    store = KisOverseasFuturesStore(tmp_path / "quotes.sqlite3")
    _ = collect_kis_overseas_futures_quotes(
        _Fetcher(
            500,
            (
                '{rt_cd:"1","msg1":"CME SUB 거래소 신청 계좌가 아닙니다.",'
                '"msg_cd":"EGW00550"}'
            ).encode(),
        ),
        store,
        request,
        _clock=lambda: OBSERVED_AT,
    )

    admission = evaluate_kis_futures_entitlement_admission(store, request)

    assert admission.status is KisFuturesAdmissionStatus.BLOCKED
    assert (
        admission.reason
        is KisFuturesAdmissionReason.CME_SUB_ENTITLEMENT_MISSING
    )
    assert admission.canonical_quote_count == 0


def test_missing_terminal_stays_unknown_without_creating_source_state(
    tmp_path: Path,
) -> None:
    request = _request()
    store = KisOverseasFuturesStore(tmp_path / "missing.sqlite3")

    admission = evaluate_kis_futures_entitlement_admission(
        store,
        request,
        _clock=lambda: OBSERVED_AT,
    )

    assert admission.status is KisFuturesAdmissionStatus.UNKNOWN
    assert (
        admission.reason
        is KisFuturesAdmissionReason.TRANSIENT_OR_MISSING_EVIDENCE
    )
    assert not store.path.exists()


def _request() -> KisFuturesQuoteRequest:
    return KisFuturesQuoteRequest(
        root_symbol="ES",
        symbols=("ESU26", "ESZ26"),
    )


def _success_payload() -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output1": {
                "proc_date": "20260724",
                "proc_time": "044500",
                "last_price": "6425.25",
                "vol": "123456",
                "bid_price": "6425.00",
                "ask_price": "6425.50",
                "prev_price": "6400.00",
                "exch_cd": "CME",
                "crc_cd": "USD",
                "trd_fr_date": "20250317",
                "expr_date": "20260918",
                "trd_to_date": "20260918",
                "sbsnsdate": "20260723",
                "sttl_price": "6401.25",
            },
        }
    ).encode()
