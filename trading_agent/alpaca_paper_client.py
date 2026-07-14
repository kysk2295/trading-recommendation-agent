from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from typing import Final, final, override

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    require_paper_trading_url,
)
from trading_agent.alpaca_paper_payloads import (
    ACCOUNT_ADAPTER,
    CLOCK_ADAPTER,
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
    PaperMarketClockSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)

MAX_ORDER_PAGE_SIZE: Final = 500
MAX_RECOVERY_ORDER_PAGES: Final = 20


@final
class AlpacaPaperClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaPaperCredentials,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        _ = require_paper_trading_url(str(client.base_url).rstrip("/"))
        if client.follow_redirects:
            raise UnsafePaperRedirectPolicyError
        self._client = client
        self._credentials = credentials
        self._clock = _clock

    def account(self) -> PaperAccountSnapshot:
        response = self._client.get("/v2/account", headers=self._headers())
        self._raise_for_status(response)
        try:
            payload = ACCOUNT_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(response.status_code, "paper 계좌 응답 형식 오류") from error
        return PaperAccountSnapshot(
            observed_at=self._clock(),
            status=payload.status,
            trading_blocked=payload.trading_blocked,
            equity=payload.equity,
            last_equity=payload.last_equity,
            buying_power=payload.buying_power,
            account_fingerprint=AccountFingerprint(
                hashlib.sha256(
                    f"{payload.id}:{payload.account_number}".encode()
                ).hexdigest()
            ),
        )

    def clock(self) -> PaperMarketClockSnapshot:
        response = self._client.get("/v2/clock", headers=self._headers())
        self._raise_for_status(response)
        try:
            payload = CLOCK_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(response.status_code, "paper 시계 응답 형식 오류") from error
        return PaperMarketClockSnapshot(
            observed_at=self._clock(),
            market_timestamp=payload.timestamp,
            is_open=payload.is_open,
            next_open=payload.next_open,
            next_close=payload.next_close,
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
        if len(payloads) == 500:
            raise PaperOrderListTruncatedError
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

    def recent_orders(
        self,
        after: dt.datetime,
    ) -> tuple[PaperOrderSnapshot, ...]:
        if after.tzinfo is None or after.utcoffset() is None:
            raise PaperOrderHistoryIncompleteError
        orders: list[PaperOrderSnapshot] = []
        seen_order_ids: set[str] = set()
        before_order_id: str | None = None
        latest_submitted_at: dt.datetime | None = None
        for _ in range(MAX_RECOVERY_ORDER_PAGES):
            params = {
                "status": "all",
                "direction": "desc",
                "limit": str(MAX_ORDER_PAGE_SIZE),
                "nested": "false",
            }
            if before_order_id is not None:
                params["before_order_id"] = before_order_id
            response = self._client.get(
                "/v2/orders",
                params=params,
                headers=self._headers(),
            )
            self._raise_for_status(response)
            try:
                payloads = ORDERS_ADAPTER.validate_json(response.content)
            except ValidationError as error:
                raise AlpacaApiError(
                    response.status_code,
                    "paper 최근 주문 목록 형식 오류",
                ) from error
            if not payloads:
                return tuple(orders)
            for payload in payloads:
                submitted_at = payload.submitted_at
                if submitted_at is None:
                    raise PaperOrderHistoryIncompleteError
                if (
                    latest_submitted_at is not None
                    and submitted_at > latest_submitted_at
                ):
                    raise PaperOrderHistoryIncompleteError
                latest_submitted_at = submitted_at
                if payload.id in seen_order_ids:
                    raise PaperOrderHistoryIncompleteError
                seen_order_ids.add(payload.id)
                if submitted_at > after:
                    orders.append(_order_snapshot(payload))
            oldest = payloads[-1].submitted_at
            if (
                len(payloads) < MAX_ORDER_PAGE_SIZE
                or oldest is None
                or oldest <= after
            ):
                return tuple(orders)
            before_order_id = payloads[-1].id
        raise PaperOrderHistoryIncompleteError

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


class UnsafePaperRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper client는 redirect를 따라가면 안 됩니다"


class PaperOrderReadIncompleteError(RuntimeError):
    pass


class PaperOrderListTruncatedError(PaperOrderReadIncompleteError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 목록이 최대 500건이라 완전성을 확인할 수 없습니다"


class PaperOrderHistoryIncompleteError(PaperOrderReadIncompleteError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 최근 주문 이력을 완전하게 페이지 순회하지 못했습니다"


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
        filled_average_price=payload.filled_avg_price,
        created_at=payload.created_at,
        updated_at=payload.updated_at,
        submitted_at=payload.submitted_at,
        filled_at=payload.filled_at,
        canceled_at=payload.canceled_at,
        failed_at=payload.failed_at,
        replaced_at=payload.replaced_at,
        replaced_by_order_id=(
            None
            if payload.replaced_by is None
            else BrokerOrderId(payload.replaced_by)
        ),
        replaces_order_id=(
            None if payload.replaces is None else BrokerOrderId(payload.replaces)
        ),
    )
