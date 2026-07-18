from __future__ import annotations

import datetime as dt
import re
import socket
from typing import Final

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_paper_config import ALPACA_PAPER_TRADING_URL
from trading_agent.alpaca_security_master_models import (
    ASSET_RESPONSE_ADAPTER,
    AlpacaSecurityMasterAsset,
    AlpacaSecurityMasterError,
    AlpacaSecurityMasterSnapshot,
    build_alpaca_security_master_snapshot,
)
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)

_VENUES: Final = {
    "AMEX": "XASE",
    "ARCA": "ARCX",
    "BATS": "BATS",
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "NYSEARCA": "ARCX",
}
_SUPPORTED_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


def create_alpaca_security_master_client() -> httpx2.Client:
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        limits=httpx2.Limits(
            max_connections=2,
            max_keepalive_connections=1,
            keepalive_expiry=15.0,
        ),
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=ALPACA_PAPER_TRADING_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )


def collect_alpaca_security_master(
    client: httpx2.Client,
    credentials: AlpacaCredentials,
    store: AlpacaSecurityMasterStore,
    *,
    observed_at: dt.datetime,
) -> AlpacaSecurityMasterSnapshot:
    try:
        _validate_boundary(client, credentials, store, observed_at)
        response = client.get(
            "/v2/assets",
            params={"status": "all", "asset_class": "us_equity"},
            headers={
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        stored = store.append_raw(observed_at, response.content)
        if response.status_code != 200 or str(response.url).split("?", 1)[0] != (
            f"{ALPACA_PAPER_TRADING_URL}/v2/assets"
        ):
            raise AlpacaSecurityMasterError
        assets = ASSET_RESPONSE_ADAPTER.validate_json(stored.raw_payload)
        instruments, aliases = _project_assets(assets, observed_at)
        snapshot = build_alpaca_security_master_snapshot(
            stored.receipt_id,
            observed_at,
            instruments,
            aliases,
        )
        store.append_snapshot(snapshot)
        return snapshot
    except (OSError, TypeError, ValidationError, ValueError):
        raise AlpacaSecurityMasterError from None


def _validate_boundary(
    client: httpx2.Client,
    credentials: AlpacaCredentials,
    store: AlpacaSecurityMasterStore,
    observed_at: dt.datetime,
) -> None:
    if (
        type(client) is not httpx2.Client
        or str(client.base_url).rstrip("/") != ALPACA_PAPER_TRADING_URL
        or client.follow_redirects
        or type(credentials) is not AlpacaCredentials
        or not credentials.key_id
        or not credentials.secret_key
        or type(store) is not AlpacaSecurityMasterStore
        or type(observed_at) is not dt.datetime
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise AlpacaSecurityMasterError


def _project_assets(
    assets: tuple[AlpacaSecurityMasterAsset, ...],
    observed_at: dt.datetime,
) -> tuple[tuple[InstrumentId, ...], tuple[InstrumentAlias, ...]]:
    selected = tuple(
        asset
        for asset in assets
        if asset.asset_class == "us_equity"
        and asset.status == "active"
        and asset.exchange in _VENUES
        and _SUPPORTED_SYMBOL.fullmatch(asset.symbol) is not None
    )
    ids = tuple(asset.asset_id for asset in selected)
    symbols = tuple(asset.symbol for asset in selected)
    if not selected or len(ids) != len(set(ids)) or len(symbols) != len(set(symbols)):
        raise AlpacaSecurityMasterError
    instruments = tuple(
        sorted(
            (
                InstrumentId(
                    value=f"alpaca:{asset.asset_id}",
                    market_domain=DataMarketDomain.US_EQUITIES,
                    asset_class=AssetClass.EQUITY,
                    venue=_VENUES[asset.exchange],
                    currency="USD",
                    timezone="America/New_York",
                    valid_from=observed_at,
                )
                for asset in selected
            ),
            key=lambda item: item.value,
        )
    )
    instrument_by_asset = {
        item.value.removeprefix("alpaca:"): item.value for item in instruments
    }
    aliases = tuple(
        sorted(
            (
                InstrumentAlias(
                    instrument_id=instrument_by_asset[asset.asset_id],
                    namespace="alpaca",
                    alias_type=InstrumentAliasType.PROVIDER_SYMBOL,
                    value=asset.symbol,
                    effective_from=observed_at,
                )
                for asset in selected
            ),
            key=lambda item: item.canonical_key,
        )
    )
    return instruments, aliases


__all__ = (
    "collect_alpaca_security_master",
    "create_alpaca_security_master_client",
)
