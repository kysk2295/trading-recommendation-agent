from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, assert_never, override
from urllib.parse import quote

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_payloads import (
    ORDER_ADAPTER,
    ORDERS_ADAPTER,
    AlpacaPaperOrderPayload,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperOrderSnapshot,
)
from trading_agent.paper_protective_oco_models import (
    PaperOpenOrderInventory,
    ProtectiveOcoClientOrderId,
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)

MAX_ORDER_PAGE_SIZE: Final = 500


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


class PaperOrderStructureIncompleteError(PaperOrderReadIncompleteError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 중첩 주문 구조가 완전한 OCO 계약과 일치하지 않습니다"


@dataclass(frozen=True, slots=True)
class AlpacaPaperOrderReader:
    client: httpx2.Client
    headers: httpx2.Headers
    clock: Callable[[], dt.datetime]

    def open_order_inventory(self) -> PaperOpenOrderInventory:
        response = self.client.get(
            "/v2/orders",
            params={"status": "open", "limit": "500", "nested": "true"},
            headers=self.headers,
        )
        require_success(response)
        payloads = parse_order_list_payload(response, "paper 주문 목록 형식 오류")
        if len(payloads) == MAX_ORDER_PAGE_SIZE:
            raise PaperOrderListTruncatedError
        return parse_order_inventory(payloads, self.clock())

    def order_by_client_id(
        self,
        client_order_id: IntentId,
    ) -> PaperOrderSnapshot | None:
        response = self.client.get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            headers=self.headers,
        )
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        require_success(response)
        return paper_order_snapshot(_parse_order_response(response))

    def order_by_id(
        self,
        broker_order_id: BrokerOrderId,
    ) -> PaperOrderSnapshot | None:
        response = self.client.get(
            f"/v2/orders/{quote(broker_order_id, safe='')}",
            headers=self.headers,
        )
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        require_success(response)
        return paper_order_snapshot(_parse_order_response(response))

    def protective_oco_by_client_id(
        self,
        client_order_id: ProtectiveOcoClientOrderId,
    ) -> ProtectiveOcoSnapshot | None:
        response = self.client.get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id, "nested": "true"},
            headers=self.headers,
        )
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        require_success(response)
        inventory = parse_order_inventory(
            (_parse_order_response(response),),
            self.clock(),
        )
        if inventory.entry_orders or len(inventory.protective_ocos) != 1:
            raise PaperOrderStructureIncompleteError
        return inventory.protective_ocos[0]


def parse_order_inventory(
    payloads: tuple[AlpacaPaperOrderPayload, ...],
    observed_at: dt.datetime,
) -> PaperOpenOrderInventory:
    entries: list[PaperOrderSnapshot] = []
    protections: list[ProtectiveOcoSnapshot] = []
    for payload in payloads:
        match payload.order_class:
            case "" | "simple":
                if payload.legs is not None:
                    raise PaperOrderStructureIncompleteError
                entries.append(paper_order_snapshot(payload))
            case "oco":
                protections.append(_protective_snapshot(payload, observed_at))
            case "bracket" | "oto" | "mleg":
                raise PaperOrderStructureIncompleteError
            case unreachable:
                assert_never(unreachable)
    return PaperOpenOrderInventory(tuple(entries), tuple(protections))


def _protective_snapshot(
    parent: AlpacaPaperOrderPayload,
    observed_at: dt.datetime,
) -> ProtectiveOcoSnapshot:
    legs = parent.legs
    if (
        parent.type != "limit"
        or parent.limit_price is None
        or parent.stop_price is not None
        or legs is None
        or len(legs) != 1
    ):
        raise PaperOrderStructureIncompleteError
    child = legs[0]
    if (
        child.type != "stop"
        or child.limit_price is not None
        or child.stop_price is None
        or child.legs is not None
        or child.symbol != parent.symbol
        or child.side is not parent.side
        or child.time_in_force != parent.time_in_force
        or child.extended_hours != parent.extended_hours
    ):
        raise PaperOrderStructureIncompleteError
    return ProtectiveOcoSnapshot(
        observed_at,
        _protective_leg(parent, ProtectiveOcoLegKind.TAKE_PROFIT),
        _protective_leg(child, ProtectiveOcoLegKind.STOP_LOSS),
    )


def _protective_leg(
    payload: AlpacaPaperOrderPayload,
    kind: ProtectiveOcoLegKind,
) -> ProtectiveOcoLegSnapshot:
    match kind:
        case ProtectiveOcoLegKind.TAKE_PROFIT:
            order_type = ProtectiveOcoOrderType.LIMIT
        case ProtectiveOcoLegKind.STOP_LOSS:
            order_type = ProtectiveOcoOrderType.STOP
        case unreachable:
            assert_never(unreachable)
    return ProtectiveOcoLegSnapshot(
        kind=kind,
        broker_order_id=BrokerOrderId(payload.id),
        client_order_id=payload.client_order_id,
        symbol=payload.symbol,
        side=payload.side,
        status=payload.status,
        quantity=payload.qty,
        filled_quantity=payload.filled_qty,
        order_type=order_type,
        limit_price=payload.limit_price,
        stop_price=payload.stop_price,
        time_in_force=payload.time_in_force,
        extended_hours=payload.extended_hours,
    )


def parse_order_list_payload(
    response: httpx2.Response,
    message: str,
) -> tuple[AlpacaPaperOrderPayload, ...]:
    try:
        return ORDERS_ADAPTER.validate_json(response.content)
    except ValidationError as error:
        raise AlpacaApiError(response.status_code, message) from error


def _parse_order_response(
    response: httpx2.Response,
) -> AlpacaPaperOrderPayload:
    try:
        return ORDER_ADAPTER.validate_json(response.content)
    except ValidationError as error:
        raise AlpacaApiError(
            response.status_code,
            "paper 주문 응답 형식 오류",
        ) from error


def require_success(response: httpx2.Response) -> None:
    if response.is_success:
        return
    raise AlpacaApiError(response.status_code, "Alpaca paper 요청 실패")


def paper_order_snapshot(payload: AlpacaPaperOrderPayload) -> PaperOrderSnapshot:
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
        replaced_by_order_id=(None if payload.replaced_by is None else BrokerOrderId(payload.replaced_by)),
        replaces_order_id=(None if payload.replaces is None else BrokerOrderId(payload.replaces)),
    )
