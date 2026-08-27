from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace

import pytest

import trading_agent.kr_autonomous_market_models as market_models
import trading_agent.kr_autonomous_market_service as market_service
from tests.test_kis_kr_market_projection import _json_body, _minute_row, _price_body, _quote_body
from tests.test_kr_social_signal_store import _signal
from trading_agent.kis_kr_market_models import KisKrMarketReceipt, KisKrMarketReceiptKind
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 26, 13, 4, 4, tzinfo=KST)


def test_current_receipts_project_content_addressed_corroboration() -> None:
    # Given: a causal social signal and one bounded current-session receipt per contract.
    source = market_service.KrCorroborationProjectionInput(
        signal=_signal(),
        calendar_snapshot=_calendar(),
        receipts=_receipts(),
        observed_at=NOW,
    )

    # When: the common live/fixture projection seam corroborates it.
    result = market_service.project_kr_corroboration(source)

    # Then: bounded current truth, content identity, and trusted-store references remain.
    assert result.task_id == source.signal.task_id
    assert result.social_signal_id == source.signal.signal_id
    assert result.calendar_snapshot_id == source.calendar_snapshot.snapshot_id
    assert result.latest_completed_bar.end_at == NOW.replace(second=0, microsecond=0)
    assert result.market_snapshot.bid_price is not None and result.market_snapshot.ask_price is not None
    assert result.receipt_count == len(result.receipt_sha256s) == 3
    assert result.evidence_count == len(result.evidence_ids) == 3
    assert result.corroboration_id == market_models.corroboration_id(result)


@pytest.mark.parametrize(
    "mutation",
    ("stale", "future", "date", "missing_quote", "missing_spread", "crossed", "symbol", "noncausal"),
)
def test_projection_rejects_invalid_current_market_evidence(mutation: str) -> None:
    # Given: one invalid receipt or chronology condition at the projection boundary.
    signal = _signal()
    receipts = list(_receipts())
    observed = NOW
    if mutation == "stale":
        receipts = [replace(item, received_at=NOW - dt.timedelta(seconds=6)) for item in receipts]
    elif mutation == "future":
        receipts[0] = replace(receipts[0], received_at=NOW + dt.timedelta(microseconds=1))
    elif mutation == "date":
        receipts = [replace(item, received_at=NOW - dt.timedelta(days=1)) for item in receipts]
    elif mutation == "missing_quote":
        receipts.pop()
    elif mutation == "missing_spread":
        document = json.loads(_quote_body(accepted_hour="130403"))
        document["output1"]["bidp1"] = "0"
        receipts[-1] = replace(receipts[-1], raw_payload=json.dumps(document).encode())
    elif mutation == "crossed":
        document = json.loads(_quote_body(accepted_hour="130403"))
        document["output1"]["bidp1"] = "104"
        receipts[-1] = replace(receipts[-1], raw_payload=json.dumps(document).encode())
    elif mutation == "symbol":
        receipts[-1] = replace(receipts[-1], symbol="000660")
    elif mutation == "noncausal":
        receipts = [replace(item, received_at=signal.first_observed_at - dt.timedelta(seconds=1)) for item in receipts]

    # When/Then: no bounded result can be projected.
    with pytest.raises(market_models.KrAutonomousMarketError):
        _ = market_service.project_kr_corroboration(
            market_service.KrCorroborationProjectionInput(
                signal=signal,
                calendar_snapshot=_calendar(),
                receipts=tuple(receipts),
                observed_at=observed,
            )
        )


@pytest.mark.parametrize("mutation", ("missing", "gap", "forming", "date"))
def test_projection_rejects_invalid_latest_completed_bar(mutation: str) -> None:
    # Given: minute evidence that cannot prove the immediately completed bar.
    receipts = list(_receipts())
    if mutation == "missing":
        receipts.pop(0)
    else:
        if mutation == "forming":
            rows = [_minute_row("130400", "101", "103", "101", "102", "100", "20200")]
        else:
            rows = [_minute_row("130200", "100", "101", "99", "100", "100", "10000")]
            rows.append(_minute_row("130300", "101", "103", "101", "102", "100", "20200"))
        for row in rows:
            row["stck_bsop_date"] = "20260826"
        if mutation == "gap":
            rows[0]["stck_cntg_hour"] = "130100"
        if mutation == "date":
            rows[1]["stck_bsop_date"] = "20260825"
        receipts[0] = replace(receipts[0], raw_payload=_json_body({"output1": {}, "output2": rows}))

    # When/Then: missing, gapped, forming, or wrong-date bars fail closed.
    with pytest.raises(market_models.KrAutonomousMarketError):
        _ = market_service.project_kr_corroboration(
            market_service.KrCorroborationProjectionInput(
                signal=_signal(),
                calendar_snapshot=_calendar(),
                receipts=tuple(receipts),
                observed_at=NOW,
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("corroboration_id", "0" * 64),
        ("symbol", "000660"),
        ("receipt_count", 2),
        ("market_response_at", NOW - dt.timedelta(hours=1)),
    ),
)
def test_model_rejects_forged_identity_field_consistency_or_time(field: str, value: str | int | dt.datetime) -> None:
    # Given: a valid result whose advertised projection is forged after construction.
    result = market_service.project_kr_corroboration(
        market_service.KrCorroborationProjectionInput(
            signal=_signal(), calendar_snapshot=_calendar(), receipts=_receipts(), observed_at=NOW
        )
    )

    # When/Then: reparsing rejects both stale IDs and internally inconsistent projections.
    with pytest.raises(ValueError):
        _ = market_models.KrAutonomousMarketCorroboration.model_validate(
            result.model_dump(mode="python") | {field: value}
        )


def test_model_rejects_readdressed_noncausal_market_response() -> None:
    # Given: a valid result readdressed after moving social observation after market response.
    result = market_service.project_kr_corroboration(
        market_service.KrCorroborationProjectionInput(
            signal=_signal(), calendar_snapshot=_calendar(), receipts=_receipts(), observed_at=NOW
        )
    )
    forged = result.model_copy(
        update={"social_first_observed_at": result.market_response_at + dt.timedelta(microseconds=1)}
    )
    payload = forged.model_copy(update={"corroboration_id": market_models.corroboration_id(forged)})

    # When/Then: even a recomputed content address cannot make noncausal chronology valid.
    with pytest.raises(ValueError):
        _ = market_models.KrAutonomousMarketCorroboration.model_validate(payload.model_dump(mode="python"))


def _receipts() -> tuple[KisKrMarketReceipt, ...]:
    rows = [
        _minute_row("130200", "100", "101", "99", "100", "100", "10000"),
        _minute_row("130300", "101", "103", "101", "102", "100", "20200"),
    ]
    for row in rows:
        row["stck_bsop_date"] = "20260826"
    payloads = (
        (KisKrMarketReceiptKind.MINUTE_BARS, _json_body({"output1": {}, "output2": rows}), 2),
        (KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), 2),
        (KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(accepted_hour="130403"), 1),
    )
    return tuple(
        KisKrMarketReceipt(
            kind=kind,
            symbol="005930",
            received_at=NOW - dt.timedelta(seconds=seconds),
            status_code=200,
            content_type="application/json",
            raw_payload=payload,
        )
        for kind, payload, seconds in payloads
    )


def _calendar(*, open_day: bool = True):
    flag = "Y" if open_day else "N"
    payload = json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "ok",
            "ctx_area_fk": "",
            "ctx_area_nk": "",
            "output": [
                {
                    "bass_dt": "20260826",
                    "wday_dvsn_cd": "3",
                    "bzdy_yn": flag,
                    "tr_day_yn": flag,
                    "opnd_yn": flag,
                    "sttl_day_yn": flag,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    receipt = KisKrSessionCalendarReceipt(
        base_date=NOW.date(),
        received_at=NOW.replace(hour=8),
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )
    return project_kis_kr_session_calendar(receipt)
