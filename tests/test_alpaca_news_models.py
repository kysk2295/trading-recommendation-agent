from __future__ import annotations

import datetime as dt

import pytest
from pydantic import JsonValue

from trading_agent.alpaca_news_models import AlpacaNewsRequest

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
type RequestInput = JsonValue | dt.datetime | tuple[str, ...]


def test_request_normalizes_bounded_symbols_and_has_stable_identity() -> None:
    request = AlpacaNewsRequest(
        collection_id="news-cycle-001",
        symbols=("TSLA", "AAPL", "TSLA"),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=8,
    )

    assert request.symbols == ("AAPL", "TSLA")
    assert len(request.request_id) == 64
    assert request.start_at == START
    assert request.end_at == END


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("symbols", ()),
        ("symbols", ("AAPL\n",)),
        ("end_at", START + dt.timedelta(days=1, seconds=1)),
        ("limit", 51),
        ("max_pages", 9),
    ),
)
def test_request_rejects_unbounded_or_unsafe_values(field: str, value: RequestInput) -> None:
    values: dict[str, RequestInput] = {
        "collection_id": "news-cycle-001",
        "symbols": ("AAPL",),
        "start_at": START,
        "end_at": END,
        "limit": 50,
        "max_pages": 8,
    }
    values[field] = value

    with pytest.raises(ValueError):
        _ = AlpacaNewsRequest.model_validate(values)
