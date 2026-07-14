from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.alpaca_paper_config import (
    ALPACA_PAPER_TRADING_URL,
    DEFAULT_ALPACA_PAPER_SECRET_PATH,
    NonPaperTradingEndpointError,
    require_paper_trading_url,
)


def test_paper_config_uses_separate_secret_file() -> None:
    # Given
    expected = Path.home() / ".config/trading-agent/alpaca-paper.env"

    # When
    actual = DEFAULT_ALPACA_PAPER_SECRET_PATH

    # Then
    assert actual == expected


def test_paper_endpoint_accepts_only_canonical_url() -> None:
    # Given
    canonical_url = ALPACA_PAPER_TRADING_URL

    # When
    actual = require_paper_trading_url(canonical_url)

    # Then
    assert actual == canonical_url


@pytest.mark.parametrize(
    "url",
    (
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.example",
        "https://paper-api.alpaca.markets/v2",
    ),
)
def test_paper_endpoint_rejects_every_noncanonical_url(url: str) -> None:
    # Given / When / Then
    with pytest.raises(NonPaperTradingEndpointError, match="paper 전용"):
        _ = require_paper_trading_url(url)
