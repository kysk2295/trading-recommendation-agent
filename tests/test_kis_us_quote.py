from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials, KisSession
from trading_agent.kis_us_quote import (
    KIS_US_LEVEL_ONE_PATH,
    KIS_US_LEVEL_ONE_TR_ID,
    KisUsQuoteUnavailableError,
    fetch_kis_us_level_one_quote,
)

NEW_YORK = ZoneInfo("America/New_York")
RECEIVED_AT = dt.datetime(2026, 7, 15, 13, 20, 1, tzinfo=NEW_YORK)
SESSION = KisSession(KisCredentials("dummy-key", "dummy-secret"), "dummy-token")


def test_fetch_kis_us_quote_uses_exact_read_only_contract() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_payload())

    quote = _fetch(handle)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == KIS_US_LEVEL_ONE_PATH
    assert dict(request.url.params) == {
        "AUTH": "",
        "EXCD": "NAS",
        "SYMB": "ABCD",
    }
    assert request.headers["tr_id"] == KIS_US_LEVEL_ONE_TR_ID
    assert request.headers["authorization"] == "Bearer dummy-token"
    assert request.headers["appkey"] == "dummy-key"
    assert request.headers["appsecret"] == "dummy-secret"
    assert quote.exchange == "NAS"
    assert quote.symbol == "ABCD"
    assert quote.provider_observed_at == RECEIVED_AT - dt.timedelta(seconds=1)
    assert quote.received_at == RECEIVED_AT
    assert quote.bid == Decimal("10.08")
    assert quote.ask == Decimal("10.10")
    assert quote.bid_size == 1_200
    assert quote.ask_size == 900


def test_fetch_kis_us_quote_ignores_unrelated_documented_fields() -> None:
    payload = _payload()
    payload["output1"]["extra_time_field"] = "ignored"
    payload["output2"]["extra_book_field"] = "ignored"
    payload["output3"] = {"provider_extension": "ignored"}

    quote = _fetch(lambda _: httpx2.Response(200, json=payload))

    assert quote.bid == Decimal("10.08")
    assert quote.ask_size == 900


@pytest.mark.parametrize("missing", ("output1", "output2"))
def test_fetch_kis_us_quote_rejects_missing_required_output_block(
    missing: str,
) -> None:
    payload = _payload()
    del payload[missing]

    _assert_failure(
        lambda _: httpx2.Response(200, json=payload),
        "invalid_response",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("dymd", "2026-07-15"),
        ("dymd", "20260230"),
        ("dhms", "13:20:00"),
        ("dhms", "256000"),
    ),
)
def test_fetch_kis_us_quote_rejects_malformed_provider_timestamp(
    field: str,
    value: str,
) -> None:
    payload = _payload()
    payload["output1"][field] = value

    _assert_failure(
        lambda _: httpx2.Response(200, json=payload),
        "invalid_timestamp",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("pbid1", "0"),
        ("pask1", "0"),
        ("pbid1", "NaN"),
        ("pask1", "Infinity"),
        ("pbid1", "10.11"),
    ),
)
def test_fetch_kis_us_quote_rejects_invalid_prices(
    field: str,
    value: str,
) -> None:
    payload = _payload()
    payload["output2"][field] = value

    _assert_failure(
        lambda _: httpx2.Response(200, json=payload),
        "invalid_quote",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("vbid1", "-1"),
        ("vask1", "1.5"),
        ("vbid1", "many"),
    ),
)
def test_fetch_kis_us_quote_rejects_invalid_sizes(
    field: str,
    value: str,
) -> None:
    payload = _payload()
    payload["output2"][field] = value

    _assert_failure(
        lambda _: httpx2.Response(200, json=payload),
        "invalid_quote",
    )


def test_fetch_kis_us_quote_rejects_non_string_provider_field() -> None:
    payload = _payload()
    payload["output2"]["pbid1"] = 10.08

    _assert_failure(
        lambda _: httpx2.Response(200, json=payload),
        "invalid_response",
    )


def test_fetch_kis_us_quote_reduces_provider_failure_to_stable_code() -> None:
    payload = _payload()
    payload["rt_cd"] = "1"
    payload["msg_cd"] = "private-code"
    payload["msg1"] = "private provider message"
    del payload["output1"]
    del payload["output2"]

    error = _assert_failure(
        lambda _: httpx2.Response(200, json=payload),
        "provider_error",
    )

    assert "private-code" not in str(error)
    assert "private provider message" not in str(error)


def test_fetch_kis_us_quote_sanitizes_http_status_failure() -> None:
    error = _assert_failure(
        lambda _: httpx2.Response(
            503,
            text="private provider response",
            headers={"x-private": "private-response-header"},
        ),
        "http_error",
    )

    _assert_sanitized(error)


def test_fetch_kis_us_quote_sanitizes_transport_exception_request() -> None:
    def reject(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("private transport detail", request=request)

    error = _assert_failure(reject, "http_error")

    _assert_sanitized(error)


def test_fetch_kis_us_quote_sanitizes_invalid_json() -> None:
    error = _assert_failure(
        lambda _: httpx2.Response(200, text='{"private-token":'),
        "invalid_json",
    )

    _assert_sanitized(error)


def test_fetch_kis_us_quote_rejects_naive_receipt_clock() -> None:
    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, json=_payload())
        ),
    ) as client, pytest.raises(KisUsQuoteUnavailableError) as caught:
        _ = fetch_kis_us_level_one_quote(
            client,
            SESSION,
            exchange="NAS",
            symbol="ABCD",
            clock=lambda: RECEIVED_AT.replace(tzinfo=None),
        )

    assert caught.value.failure_code == "invalid_clock"
    _assert_sanitized(caught.value)


def _fetch(
    handler: Callable[[httpx2.Request], httpx2.Response],
):
    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handler),
    ) as client:
        return fetch_kis_us_level_one_quote(
            client,
            SESSION,
            exchange="NAS",
            symbol="ABCD",
            clock=lambda: RECEIVED_AT,
        )


def _assert_failure(
    handler: Callable[[httpx2.Request], httpx2.Response],
    failure_code: str,
) -> KisUsQuoteUnavailableError:
    with pytest.raises(KisUsQuoteUnavailableError) as caught:
        _ = _fetch(handler)
    assert caught.value.failure_code == failure_code
    return caught.value


def _assert_sanitized(error: KisUsQuoteUnavailableError) -> None:
    rendered = f"{error!s} {error!r}"
    assert "dummy-key" not in rendered
    assert "dummy-secret" not in rendered
    assert "dummy-token" not in rendered
    assert "private" not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def _payload() -> dict[str, Any]:
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "provider success text",
        "output1": {"dymd": "20260715", "dhms": "132000"},
        "output2": {
            "pbid1": "10.08",
            "pask1": "10.10",
            "vbid1": "1200",
            "vask1": "900",
        },
        "output3": {},
    }
