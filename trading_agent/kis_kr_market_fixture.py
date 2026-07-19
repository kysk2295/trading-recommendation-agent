from __future__ import annotations

import datetime as dt
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, final, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kis_kr_market_client import KisKrMarketFetchRequest
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
)
from trading_agent.kr_instrument import is_kr_instrument_symbol_v2


class InvalidKisKrMarketFixtureError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market fixture is invalid"


class KisKrMarketFixtureReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: KisKrMarketReceiptKind
    received_at: dt.datetime
    payload_path: str

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            not _aware(self.received_at)
            or not self.payload_path
            or self.payload_path != self.payload_path.strip()
            or Path(self.payload_path).is_absolute()
        ):
            raise InvalidKisKrMarketFixtureError
        return self


class KisKrMarketFixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    requested_at: dt.datetime
    receipts: tuple[KisKrMarketFixtureReceipt, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        kinds = tuple(item.kind for item in self.receipts)
        if (
            not is_kr_instrument_symbol_v2(self.symbol)
            or not _aware(self.requested_at)
            or set(kinds) != set(KisKrMarketReceiptKind)
            or len(kinds) != len(set(kinds))
            or any(item.received_at < self.requested_at for item in self.receipts)
        ):
            raise InvalidKisKrMarketFixtureError
        return self


@dataclass(frozen=True, slots=True)
class LoadedKisKrMarketFixture:
    manifest: KisKrMarketFixtureManifest
    fetcher: FixtureKisKrMarketFetcher


@final
class FixtureKisKrMarketFetcher:
    __slots__ = ("_manifest", "_receipts")

    def __init__(
        self,
        manifest: KisKrMarketFixtureManifest,
        receipts: tuple[KisKrMarketReceipt, ...],
    ) -> None:
        self._manifest = manifest
        self._receipts = receipts

    def fetch(self, request: KisKrMarketFetchRequest) -> KisKrMarketReceipt:
        matches = tuple(receipt for receipt in self._receipts if receipt.kind is request.kind)
        if (
            request.symbol != self._manifest.symbol
            or request.requested_at != self._manifest.requested_at
            or len(matches) != 1
        ):
            raise InvalidKisKrMarketFixtureError
        return matches[0]


def load_kis_kr_market_fixture(path: Path) -> LoadedKisKrMarketFixture:
    try:
        _require_regular(path)
        manifest = KisKrMarketFixtureManifest.model_validate_json(path.read_text(encoding="utf-8"))
        root = path.parent.resolve(strict=True)
        receipts = tuple(_receipt(root, manifest, item) for item in manifest.receipts)
        return LoadedKisKrMarketFixture(manifest, FixtureKisKrMarketFetcher(manifest, receipts))
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKisKrMarketFixtureError from None


def _receipt(
    root: Path,
    manifest: KisKrMarketFixtureManifest,
    item: KisKrMarketFixtureReceipt,
) -> KisKrMarketReceipt:
    payload = root / item.payload_path
    _require_regular(payload)
    resolved = payload.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise InvalidKisKrMarketFixtureError
    return KisKrMarketReceipt(
        kind=item.kind,
        symbol=manifest.symbol,
        received_at=item.received_at,
        status_code=200,
        content_type="application/json",
        raw_payload=resolved.read_bytes(),
    )


def _require_regular(path: Path) -> None:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise InvalidKisKrMarketFixtureError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
