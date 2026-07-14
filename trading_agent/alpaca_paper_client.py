from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from typing import final, override

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_activities import read_fill_activities
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    require_paper_trading_url,
)
from trading_agent.alpaca_paper_order_history import AlpacaPaperOrderHistoryReader
from trading_agent.alpaca_paper_order_reads import (
    AlpacaPaperOrderReader,
)
from trading_agent.alpaca_paper_order_reads import (
    PaperOrderHistoryIncompleteError as PaperOrderHistoryIncompleteError,
)
from trading_agent.alpaca_paper_order_reads import (
    PaperOrderListTruncatedError as PaperOrderListTruncatedError,
)
from trading_agent.alpaca_paper_order_reads import (
    PaperOrderReadIncompleteError as PaperOrderReadIncompleteError,
)
from trading_agent.alpaca_paper_order_reads import (
    PaperOrderStructureIncompleteError as PaperOrderStructureIncompleteError,
)
from trading_agent.alpaca_paper_payloads import (
    ACCOUNT_ADAPTER,
    CLOCK_ADAPTER,
    POSITIONS_ADAPTER,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    IntentId,
    PaperAccountSnapshot,
    PaperMarketClockSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
    PaperTradeActivity,
)
from trading_agent.paper_protective_oco_models import PaperOpenOrderInventory


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
                hashlib.sha256(f"{payload.id}:{payload.account_number}".encode()).hexdigest()
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
        return self.open_order_inventory().entry_orders

    def open_order_inventory(self) -> PaperOpenOrderInventory:
        return AlpacaPaperOrderReader(
            self._client,
            self._headers(),
            self._clock,
        ).open_order_inventory()

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
        return self.recent_order_inventory(after).entry_orders

    def recent_order_inventory(
        self,
        after: dt.datetime,
    ) -> PaperOpenOrderInventory:
        return AlpacaPaperOrderHistoryReader(
            self._client,
            self._headers(),
            self._clock,
        ).recent_order_inventory(after)

    def order_by_client_id(self, client_order_id: IntentId) -> PaperOrderSnapshot | None:
        return AlpacaPaperOrderReader(
            self._client,
            self._headers(),
            self._clock,
        ).order_by_client_id(client_order_id)

    def fill_activities(
        self,
        after: dt.datetime,
    ) -> tuple[PaperTradeActivity, ...]:
        return read_fill_activities(self._client, self._credentials, after)

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
