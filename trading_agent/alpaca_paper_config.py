from __future__ import annotations

from pathlib import Path
from typing import Final, override

ALPACA_PAPER_TRADING_URL: Final = "https://paper-api.alpaca.markets"
DEFAULT_ALPACA_PAPER_SECRET_PATH: Final = (
    Path.home() / ".config/trading-agent/alpaca-paper.env"
)


class NonPaperTradingEndpointError(ValueError):
    __slots__ = ("url",)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    @override
    def __str__(self) -> str:
        return "Alpaca 거래 주소는 paper 전용 고정값이어야 합니다"


def require_paper_trading_url(url: str) -> str:
    if url != ALPACA_PAPER_TRADING_URL:
        raise NonPaperTradingEndpointError(url)
    return url
