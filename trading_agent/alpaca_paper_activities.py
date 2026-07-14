from __future__ import annotations

import datetime as dt
from typing import Final, override

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    require_paper_trading_url,
)
from trading_agent.alpaca_paper_payloads import TRADE_ACTIVITIES_ADAPTER
from trading_agent.paper_execution_models import (
    AccountActivityId,
    BrokerOrderId,
    PaperTradeActivity,
)

ACTIVITY_PAGE_SIZE: Final = 100
MAX_ACTIVITY_PAGES: Final = 100


class PaperActivityHistoryIncompleteError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper FILL 활동 이력을 완전하게 페이지 순회하지 못했습니다"


def read_fill_activities(
    client: httpx2.Client,
    credentials: AlpacaPaperCredentials,
    after: dt.datetime,
) -> tuple[PaperTradeActivity, ...]:
    _ = require_paper_trading_url(str(client.base_url).rstrip("/"))
    if after.tzinfo is None or after.utcoffset() is None:
        raise PaperActivityHistoryIncompleteError
    activities: list[PaperTradeActivity] = []
    seen_ids: set[AccountActivityId] = set()
    page_token: str | None = None
    latest_time: dt.datetime | None = None
    for _ in range(MAX_ACTIVITY_PAGES):
        params = {
            "after": after.isoformat(),
            "direction": "asc",
            "page_size": str(ACTIVITY_PAGE_SIZE),
        }
        if page_token is not None:
            params["page_token"] = page_token
        response = client.get(
            "/v2/account/activities/FILL",
            params=params,
            headers={
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        if not response.is_success:
            raise AlpacaApiError(response.status_code, "Alpaca paper 요청 실패")
        try:
            payloads = TRADE_ACTIVITIES_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise AlpacaApiError(
                response.status_code,
                "paper FILL 활동 목록 형식 오류",
            ) from error
        if not payloads:
            return tuple(activities)
        for payload in payloads:
            activity_id = AccountActivityId(payload.id)
            if activity_id in seen_ids:
                raise PaperActivityHistoryIncompleteError
            if latest_time is not None and payload.transaction_time < latest_time:
                raise PaperActivityHistoryIncompleteError
            seen_ids.add(activity_id)
            latest_time = payload.transaction_time
            activities.append(
                PaperTradeActivity(
                    activity_id=activity_id,
                    broker_order_id=BrokerOrderId(payload.order_id),
                    symbol=payload.symbol,
                    side=payload.side,
                    event_type=payload.type,
                    quantity=payload.qty,
                    cumulative_quantity=payload.cum_qty,
                    leaves_quantity=payload.leaves_qty,
                    price=payload.price,
                    transaction_time=payload.transaction_time,
                    payload_json=payload.model_dump_json(),
                )
            )
        if len(payloads) < ACTIVITY_PAGE_SIZE:
            return tuple(activities)
        page_token = payloads[-1].id
    raise PaperActivityHistoryIncompleteError
