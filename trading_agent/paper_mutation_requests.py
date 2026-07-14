from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import quote

from trading_agent.paper_execution_models import SizedPaperOrder
from trading_agent.paper_protective_oco_models import ProtectiveOcoExitPlan
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
)


@dataclass(frozen=True, slots=True)
class PaperMutationHttpRequest:
    method: str
    path: str
    params: tuple[tuple[str, str], ...]
    body: bytes | None

    @property
    def sha256(self) -> str:
        material = json.dumps(
            (
                self.method,
                self.path,
                self.params,
                None if self.body is None else self.body.decode("ascii"),
            ),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode()).hexdigest()


def entry_order_request(order: SizedPaperOrder) -> PaperMutationHttpRequest:
    intent = order.intent
    body = json.dumps(
        {
            "client_order_id": intent.intent_id,
            "symbol": intent.symbol,
            "qty": str(order.quantity),
            "side": intent.side.value,
            "type": "limit",
            "time_in_force": "day",
            "order_class": "simple",
            "limit_price": str(intent.entry_limit),
            "extended_hours": False,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return PaperMutationHttpRequest("POST", "/v2/orders", (), body)


def protective_oco_request(
    plan: ProtectiveOcoExitPlan,
) -> PaperMutationHttpRequest:
    body = json.dumps(
        {
            "client_order_id": plan.client_order_id,
            "symbol": plan.symbol,
            "qty": str(plan.quantity),
            "side": plan.side.value,
            "type": plan.order_type,
            "time_in_force": plan.time_in_force,
            "order_class": plan.order_class,
            "extended_hours": plan.extended_hours,
            "take_profit": {"limit_price": str(plan.take_profit_limit)},
            "stop_loss": {"stop_price": str(plan.stop_price)},
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return PaperMutationHttpRequest("POST", "/v2/orders", (), body)


def cancel_order_request(
    action: PaperCancelOrderAction,
) -> PaperMutationHttpRequest:
    path = f"/v2/orders/{quote(action.broker_order_id, safe='')}"
    return PaperMutationHttpRequest("DELETE", path, (), None)


def close_position_request(
    action: PaperClosePositionAction,
) -> PaperMutationHttpRequest:
    path = f"/v2/positions/{quote(action.symbol, safe='')}"
    return PaperMutationHttpRequest(
        "DELETE",
        path,
        (("qty", str(action.quantity)),),
        None,
    )
