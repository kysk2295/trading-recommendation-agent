from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import httpx2

from trading_agent.alpaca_paper_order_reads import (
    MAX_ORDER_PAGE_SIZE,
    PaperOrderHistoryIncompleteError,
    parse_order_inventory,
    parse_order_list_payload,
    require_success,
)
from trading_agent.paper_protective_oco_models import PaperOpenOrderInventory

MAX_RECOVERY_ORDER_PAGES: Final = 20


@dataclass(frozen=True, slots=True)
class AlpacaPaperOrderHistoryReader:
    client: httpx2.Client
    headers: httpx2.Headers
    clock: Callable[[], dt.datetime]

    def recent_order_inventory(
        self,
        after: dt.datetime,
    ) -> PaperOpenOrderInventory:
        if after.tzinfo is None or after.utcoffset() is None:
            raise PaperOrderHistoryIncompleteError
        entry_orders = []
        protective_ocos = []
        seen_order_ids: set[str] = set()
        before_order_id: str | None = None
        latest_submitted_at: dt.datetime | None = None
        for _ in range(MAX_RECOVERY_ORDER_PAGES):
            params = {
                "status": "all",
                "direction": "desc",
                "limit": str(MAX_ORDER_PAGE_SIZE),
                "nested": "true",
            }
            if before_order_id is not None:
                params["before_order_id"] = before_order_id
            response = self.client.get(
                "/v2/orders",
                params=params,
                headers=self.headers,
            )
            require_success(response)
            payloads = parse_order_list_payload(
                response,
                "paper 최근 주문 목록 형식 오류",
            )
            if not payloads:
                return PaperOpenOrderInventory(
                    tuple(entry_orders),
                    tuple(protective_ocos),
                )
            selected = []
            for payload in payloads:
                submitted_at = payload.submitted_at
                if submitted_at is None:
                    raise PaperOrderHistoryIncompleteError
                if latest_submitted_at is not None and submitted_at > latest_submitted_at:
                    raise PaperOrderHistoryIncompleteError
                latest_submitted_at = submitted_at
                if payload.id in seen_order_ids:
                    raise PaperOrderHistoryIncompleteError
                seen_order_ids.add(payload.id)
                if submitted_at > after:
                    selected.append(payload)
            inventory = parse_order_inventory(tuple(selected), self.clock())
            entry_orders.extend(inventory.entry_orders)
            protective_ocos.extend(inventory.protective_ocos)
            oldest = payloads[-1].submitted_at
            if len(payloads) < MAX_ORDER_PAGE_SIZE or oldest is None or oldest <= after:
                return PaperOpenOrderInventory(
                    tuple(entry_orders),
                    tuple(protective_ocos),
                )
            before_order_id = payloads[-1].id
        raise PaperOrderHistoryIncompleteError
