from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Final

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from trading_agent.alpaca_http import AlpacaApiError, AlpacaCredentials

ALPACA_TRADING_URL: Final = "https://paper-api.alpaca.markets"
LISTED_EXCHANGES: Final = frozenset(
    {
        "AMEX",
        "ARCA",
        "BATS",
        "NASDAQ",
        "NYSE",
        "NYSEARCA",
    }
)
STOCK_BAR_SYMBOL_PATTERN: Final = re.compile(r"[A-Z]+(?:\.[A-Z]+)?")


class AlpacaAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(alias="id")
    asset_class: str = Field(alias="class")
    exchange: str
    symbol: str
    name: str
    status: str
    tradable: bool


ASSETS_ADAPTER: Final = TypeAdapter(tuple[AlpacaAsset, ...])


def fetch_alpaca_universe(
    client: httpx2.Client,
    credentials: AlpacaCredentials,
) -> tuple[AlpacaAsset, ...]:
    response = client.get(
        "/v2/assets",
        params={"status": "all", "asset_class": "us_equity"},
        headers={
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        },
    )
    if response.status_code >= 400:
        raise AlpacaApiError(
            status_code=response.status_code,
            message=response.reason_phrase,
        )
    assets = ASSETS_ADAPTER.validate_json(response.content)
    return tuple(
        sorted(
            (
                asset
                for asset in assets
                if asset.asset_class == "us_equity"
                and asset.exchange in LISTED_EXCHANGES
                and STOCK_BAR_SYMBOL_PATTERN.fullmatch(asset.symbol) is not None
            ),
            key=lambda asset: asset.symbol,
        )
    )


def write_universe_snapshot(path: Path, assets: tuple[AlpacaAsset, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("symbol", "status", "exchange", "tradable", "asset_id", "name"))
        writer.writerows(
            (
                asset.symbol,
                asset.status,
                asset.exchange,
                asset.tradable,
                asset.asset_id,
                asset.name,
            )
            for asset in assets
        )
    temporary_path.replace(path)
