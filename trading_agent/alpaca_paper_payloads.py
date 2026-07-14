from __future__ import annotations

from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, TypeAdapter

from trading_agent.paper_execution_models import PaperOrderSide


class AlpacaPaperAccountPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    trading_blocked: bool


class AlpacaPaperOrderPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    client_order_id: str
    symbol: str
    side: PaperOrderSide
    status: str
    qty: Decimal
    filled_qty: Decimal
    limit_price: Decimal | None
    time_in_force: str
    extended_hours: bool


class AlpacaPaperPositionPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: Decimal
    market_value: Decimal


ACCOUNT_ADAPTER: Final = TypeAdapter(AlpacaPaperAccountPayload)
ORDER_ADAPTER: Final = TypeAdapter(AlpacaPaperOrderPayload)
ORDERS_ADAPTER: Final = TypeAdapter(tuple[AlpacaPaperOrderPayload, ...])
POSITIONS_ADAPTER: Final = TypeAdapter(tuple[AlpacaPaperPositionPayload, ...])
