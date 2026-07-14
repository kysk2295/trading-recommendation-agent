from __future__ import annotations

import datetime as dt
import hashlib
from typing import final

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    require_paper_trading_url,
)
from trading_agent.alpaca_paper_payloads import (
    ACCOUNT_ADAPTER,
    ORDER_ADAPTER,
    ORDERS_ADAPTER,
    POSITIONS_ADAPTER,
    AlpacaPaperOrderPayload,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)


@final
class AlpacaPaperClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaPaperCredentials,
    ) -> None:
        _ = require_paper_trading_url(str(client.base_url).rstrip("/"))
        self._client = client
        self._credentials = credentials

    def account(self, observed_at: dt.datetime) -> PaperAccountSnapshot:
        response = self._client.get("/v2/account", headers=self._headers())
        self._raise_for_status(response)
        try:
            payload = ACCOUNT_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(response.status_code, "paper 계좌 응답 형식 오류") from error
        return PaperAccountSnapshot(
            observed_at=observed_at,
            status=payload.status,
            trading_blocked=payload.trading_blocked,
            account_fingerprint=AccountFingerprint(
                hashlib.sha256(
                    f"{payload.id}:{payload.account_number}".encode()
                ).hexdigest()
            ),
        )

    def open_orders(self) -> tuple[PaperOrderSnapshot, ...]:
        response = self._client.get(
            "/v2/orders",
            params={"status": "open", "limit": "500"},
            headers=self._headers(),
        )
        self._raise_for_status(response)
        try:
            payloads = ORDERS_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(response.status_code, "paper 주문 목록 형식 오류") from error
        return tuple(_order_snapshot(payload) for payload in payloads)

    def positions(self) -> tuple[PaperPositionSnapshot, ...]:
        response = self._client.get("/v2/positions", headers=self._headers())
        self._raise_for_status(response)
        try:
            payloads = POSITIONS_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(response.status_code, "paper 포지션 목록 형식 오류") from error
        return tuple(
            PaperPositionSnapshot(
                symbol=payload.symbol,
                quantity=payload.qty,
                market_value=payload.market_value,
            )
            for payload in payloads
        )

    def order_by_client_id(self, client_order_id: IntentId) -> PaperOrderSnapshot | None:
        response = self._client.get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            headers=self._headers(),
        )
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        self._raise_for_status(response)
        try:
            payload = ORDER_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(response.status_code, "paper 주문 응답 형식 오류") from error
        return _order_snapshot(payload)

    def _headers(self) -> httpx2.Headers:
        return httpx2.Headers(
            {
                "APCA-API-KEY-ID": self._credentials.key_id,
                "APCA-API-SECRET-KEY": self._credentials.secret_key,
            }
        )

    @staticmethod
    def _raise_for_status(response: httpx2.Response) -> None:
        if response.is_success:
            return
        raise AlpacaApiError(response.status_code, "Alpaca paper 요청 실패")


def _order_snapshot(payload: AlpacaPaperOrderPayload) -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        broker_order_id=BrokerOrderId(payload.id),
        client_order_id=IntentId(payload.client_order_id),
        symbol=payload.symbol,
        side=payload.side,
        status=payload.status,
        quantity=payload.qty,
        filled_quantity=payload.filled_qty,
        limit_price=payload.limit_price,
        time_in_force=payload.time_in_force,
        extended_hours=payload.extended_hours,
    )
