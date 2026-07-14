from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import final, override

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    require_paper_trading_url,
)
from trading_agent.alpaca_paper_order_reads import (
    PaperOrderStructureIncompleteError,
    paper_order_snapshot,
    parse_order_inventory,
)
from trading_agent.alpaca_paper_payloads import ORDER_ADAPTER
from trading_agent.paper_execution_models import SizedPaperOrder
from trading_agent.paper_mutation_models import (
    PaperCancelOrderReceipt,
    PaperClosePositionReceipt,
    PaperEntryOrderReceipt,
    PaperMutationRequestId,
    PaperProtectiveOcoReceipt,
)
from trading_agent.paper_mutation_request_validation import (
    InvalidPaperMutationRequestError as InvalidPaperMutationRequestError,
)
from trading_agent.paper_mutation_request_validation import (
    require_cancel_action,
    require_close_action,
    require_entry_order,
    require_oco_plan,
)
from trading_agent.paper_mutation_requests import (
    PaperMutationHttpRequest,
    cancel_order_request,
    close_position_request,
    entry_order_request,
    protective_oco_request,
)
from trading_agent.paper_protective_oco_models import ProtectiveOcoExitPlan
from trading_agent.paper_protective_oco_store import (
    protective_oco_snapshot_matches_plan,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
)


class PaperMutationResponseError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"Alpaca Paper mutation 응답이 불완전합니다: {self.reason}"


class PaperMutationRejectedError(RuntimeError):
    __slots__ = ("request_id", "status_code")

    def __init__(
        self,
        status_code: int,
        request_id: PaperMutationRequestId | None,
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self.request_id = request_id

    @override
    def __str__(self) -> str:
        return f"Alpaca Paper mutation이 거부됐습니다: HTTP {self.status_code}"


class UnsafePaperMutationRedirectError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper mutation client는 redirect를 따라가면 안 됩니다"


@final
class AlpacaPaperMutationClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaPaperCredentials,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        _ = require_paper_trading_url(str(client.base_url).rstrip("/"))
        if client.follow_redirects:
            raise UnsafePaperMutationRedirectError
        self._client = client
        self._credentials = credentials
        self._clock = _clock

    def submit_protective_oco(
        self,
        plan: ProtectiveOcoExitPlan,
    ) -> PaperProtectiveOcoReceipt:
        require_oco_plan(plan)
        response = self._send(protective_oco_request(plan))
        _require_status(response, httpx2.codes.OK)
        request_id = _request_id(response)
        observed_at = _aware_now(self._clock)
        try:
            payload = ORDER_ADAPTER.validate_json(response.content)
            inventory = parse_order_inventory((payload,), observed_at)
        except (ValidationError, PaperOrderStructureIncompleteError) as error:
            raise PaperMutationResponseError("보호 OCO 형식") from error
        if len(inventory.protective_ocos) != 1 or inventory.entry_orders:
            raise PaperMutationResponseError("보호 OCO cardinality")
        snapshot = inventory.protective_ocos[0]
        if not protective_oco_snapshot_matches_plan(snapshot, plan):
            raise PaperMutationResponseError("보호 OCO 계획 불일치")
        return PaperProtectiveOcoReceipt(request_id, snapshot)

    def submit_entry(self, order: SizedPaperOrder) -> PaperEntryOrderReceipt:
        require_entry_order(order)
        response = self._send(entry_order_request(order))
        _require_status(response, httpx2.codes.OK)
        request_id = _request_id(response)
        received_at = _aware_now(self._clock)
        try:
            payload = ORDER_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise PaperMutationResponseError("진입 주문 형식") from error
        snapshot = paper_order_snapshot(payload)
        intent = order.intent
        if (
            snapshot.client_order_id != intent.intent_id
            or snapshot.symbol != intent.symbol
            or snapshot.side is not intent.side
            or snapshot.quantity != order.quantity
            or snapshot.limit_price != Decimal(str(intent.entry_limit))
            or payload.type != "limit"
            or payload.order_class not in ("", "simple")
            or snapshot.time_in_force != "day"
            or snapshot.extended_hours
            or payload.stop_price is not None
            or payload.legs is not None
        ):
            raise PaperMutationResponseError("진입 주문 불일치")
        return PaperEntryOrderReceipt(request_id, received_at, snapshot)

    def cancel_order(
        self,
        action: PaperCancelOrderAction,
    ) -> PaperCancelOrderReceipt:
        require_cancel_action(action)
        response = self._send(cancel_order_request(action))
        _require_status(response, httpx2.codes.NO_CONTENT)
        return PaperCancelOrderReceipt(
            _request_id(response),
            action.broker_order_id,
            _aware_now(self._clock),
        )

    def close_position(
        self,
        action: PaperClosePositionAction,
    ) -> PaperClosePositionReceipt:
        require_close_action(action)
        response = self._send(close_position_request(action))
        _require_status(response, httpx2.codes.OK)
        request_id = _request_id(response)
        received_at = _aware_now(self._clock)
        try:
            payload = ORDER_ADAPTER.validate_json(response.content)
        except ValidationError as error:
            raise PaperMutationResponseError("평탄화 주문 형식") from error
        if (
            payload.symbol != action.symbol
            or payload.side is not action.side
            or payload.qty != action.quantity
            or payload.type != "market"
            or payload.order_class not in ("", "simple")
            or payload.time_in_force != "day"
            or payload.extended_hours
            or payload.limit_price is not None
            or payload.stop_price is not None
            or payload.legs is not None
        ):
            raise PaperMutationResponseError("평탄화 주문 불일치")
        return PaperClosePositionReceipt(
            request_id,
            received_at,
            paper_order_snapshot(payload),
        )

    def _headers(self) -> httpx2.Headers:
        return httpx2.Headers(
            {
                "APCA-API-KEY-ID": self._credentials.key_id,
                "APCA-API-SECRET-KEY": self._credentials.secret_key,
            }
        )

    def _send(self, request: PaperMutationHttpRequest) -> httpx2.Response:
        headers = self._headers()
        if request.body is not None:
            headers["Content-Type"] = "application/json"
        return self._client.request(
            request.method,
            request.path,
            params=request.params,
            content=request.body,
            headers=headers,
        )


def _request_id(response: httpx2.Response) -> PaperMutationRequestId:
    value = _optional_request_id(response)
    if value is None:
        raise PaperMutationResponseError("request ID 누락")
    return value


def _optional_request_id(
    response: httpx2.Response,
) -> PaperMutationRequestId | None:
    value = response.headers.get("X-Request-ID", "")
    if not value or value.strip() != value or len(value) > 128:
        return None
    return PaperMutationRequestId(value)


def _require_status(response: httpx2.Response, expected: httpx2.codes) -> None:
    if response.status_code != expected:
        raise PaperMutationRejectedError(
            response.status_code,
            _optional_request_id(response),
        )


def _aware_now(clock: Callable[[], dt.datetime]) -> dt.datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise PaperMutationResponseError("수신시각 timezone 누락")
    return value
