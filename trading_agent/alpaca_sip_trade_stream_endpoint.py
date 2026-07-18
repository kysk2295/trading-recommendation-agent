from __future__ import annotations

import ssl
from typing import Final

from websockets.http11 import Request
from websockets.sync.connection import Connection

from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamEndpointError

ALPACA_SIP_TRADE_STREAM_URL: Final = "wss://stream.data.alpaca.markets/v2/sip"


def require_alpaca_sip_trade_stream_url(url: str) -> str:
    if url != ALPACA_SIP_TRADE_STREAM_URL:
        raise AlpacaSipTradeStreamEndpointError
    return url


def final_alpaca_sip_connection_url(connection: Connection, request: Request | None) -> str:
    if request is None:
        return ""
    scheme = "wss" if isinstance(connection.socket, ssl.SSLSocket) else "ws"
    host = request.headers.get("Host", "")
    return f"{scheme}://{host}{request.path}"


__all__ = (
    "ALPACA_SIP_TRADE_STREAM_URL",
    "final_alpaca_sip_connection_url",
    "require_alpaca_sip_trade_stream_url",
)
