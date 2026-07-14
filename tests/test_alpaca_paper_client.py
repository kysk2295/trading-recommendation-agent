from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_client import (
    AlpacaPaperClient,
    UnsafePaperRedirectPolicyError,
)
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    NonPaperTradingEndpointError,
)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _order_json() -> dict[str, str | bool | None]:
    return {
        "id": "paper-order-1",
        "client_order_id": "orb-v1-20260714-AAA-093600",
        "symbol": "AAA",
        "side": "buy",
        "status": "partially_filled",
        "qty": "259",
        "filled_qty": "0.5",
        "filled_avg_price": "10.05",
        "limit_price": "10.0000",
        "time_in_force": "day",
        "extended_hours": False,
        "created_at": "2026-07-14T13:35:00Z",
        "updated_at": "2026-07-14T13:36:00.123456789Z",
        "submitted_at": "2026-07-14T13:35:01Z",
        "filled_at": None,
        "canceled_at": None,
        "failed_at": None,
        "replaced_at": None,
        "replaced_by": "paper-order-2",
        "replaces": None,
    }


def test_client_rejects_live_base_url_before_request() -> None:
    # Given
    def reject_request(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"HTTP must not run: {request.url}")

    # When / Then
    with httpx2.Client(
        base_url="https://api.alpaca.markets",
        transport=httpx2.MockTransport(reject_request),
    ) as http_client, pytest.raises(
        NonPaperTradingEndpointError,
        match="paper 전용",
    ):
        _ = AlpacaPaperClient(http_client, _credentials())


def test_client_rejects_redirect_enabled_http_client() -> None:
    # Given / When / Then
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        follow_redirects=True,
    ) as http_client, pytest.raises(
        UnsafePaperRedirectPolicyError,
        match="redirect",
    ):
        _ = AlpacaPaperClient(http_client, _credentials())


def test_account_snapshot_discards_private_account_identifiers() -> None:
    # Given
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["APCA-API-KEY-ID"] == "test-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "test-secret"
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "private-account-id",
                "account_number": "private-account-number",
                "status": "ACTIVE",
                "trading_blocked": False,
                "equity": "30000.00",
                "last_equity": "29950.00",
                "buying_power": "12000.00",
            },
        )

    # When
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        snapshot = AlpacaPaperClient(
            http_client,
            _credentials(),
            _clock=lambda: dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        ).account()

    # Then
    assert snapshot.status == "ACTIVE"
    assert snapshot.trading_blocked is False
    assert snapshot.equity == Decimal("30000.00")
    assert snapshot.last_equity == Decimal("29950.00")
    assert snapshot.buying_power == Decimal("12000.00")
    assert len(snapshot.account_fingerprint) == 64
    assert "private-account" not in repr(snapshot)


def test_clock_snapshot_preserves_broker_time_and_receipt_time() -> None:
    # Given
    observed_at = dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC)

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v2/clock"
        return httpx2.Response(
            200,
            request=request,
            json={
                "timestamp": "2026-07-14T09:36:00-04:00",
                "is_open": True,
                "next_open": "2026-07-15T09:30:00-04:00",
                "next_close": "2026-07-14T16:00:00-04:00",
            },
        )

    # When
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        snapshot = AlpacaPaperClient(
            http_client,
            _credentials(),
            _clock=lambda: observed_at,
        ).clock()

    # Then
    assert snapshot.observed_at == observed_at
    assert snapshot.market_timestamp == dt.datetime(
        2026,
        7,
        14,
        9,
        36,
        tzinfo=dt.timezone(dt.timedelta(hours=-4)),
    )
    assert snapshot.is_open is True


def test_clock_rejects_broker_timestamps_without_timezone_offsets() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            json={
                "timestamp": "2026-07-14T09:36:00",
                "is_open": True,
                "next_open": "2026-07-15T09:30:00",
                "next_close": "2026-07-14T16:00:00",
            },
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client, pytest.raises(AlpacaApiError, match="시계 응답 형식"):
        _ = AlpacaPaperClient(http_client, _credentials()).clock()


def test_order_and_position_reads_preserve_fractional_quantities() -> None:
    # Given
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v2/orders":
            return httpx2.Response(200, request=request, json=[_order_json()])
        return httpx2.Response(
            200,
            request=request,
            json=[{"symbol": "AAA", "qty": "0.5", "market_value": "5.00"}],
        )

    # When
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        client = AlpacaPaperClient(http_client, _credentials())
        orders = client.open_orders()
        positions = client.positions()

    # Then
    assert orders[0].filled_quantity == Decimal("0.5")
    assert orders[0].filled_average_price == Decimal("10.05")
    assert orders[0].updated_at == dt.datetime(
        2026,
        7,
        14,
        13,
        36,
        0,
        123456,
        tzinfo=dt.UTC,
    )
    assert orders[0].replaced_by_order_id == "paper-order-2"
    assert positions[0].quantity == Decimal("0.5")


def test_client_does_not_follow_redirects_with_custom_auth_headers() -> None:
    # Given
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            307,
            request=request,
            headers={"location": "https://example.invalid/steal"},
        )

    # When / Then
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        client = AlpacaPaperClient(http_client, _credentials())
        with pytest.raises(AlpacaApiError):
            _ = client.account()
    assert len(requests) == 1


def test_api_failure_never_renders_credentials() -> None:
    # Given
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            request=request,
            json={"message": "forbidden"},
        )

    # When
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        client = AlpacaPaperClient(http_client, _credentials())
        with pytest.raises(AlpacaApiError) as captured:
            _ = client.account()

    # Then
    rendered = str(captured.value)
    assert "403" in rendered
    assert "test-key" not in rendered
    assert "test-secret" not in rendered


def test_foundation_client_exposes_no_order_mutation_methods() -> None:
    # Given
    with httpx2.Client(base_url="https://paper-api.alpaca.markets") as http_client:
        client = AlpacaPaperClient(http_client, _credentials())

        # When
        public_names = {name for name in dir(client) if not name.startswith("_")}

    # Then
    assert "submit_limit_order" not in public_names
    assert "cancel_order" not in public_names
    assert "close_position" not in public_names
