from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.alpaca_news_client import AlpacaNewsTransportError
from trading_agent.alpaca_news_models import (
    ALPACA_NEWS_MAX_RAW_BYTES,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_ENCODING = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


class AlpacaNewsFixtureError(AlpacaNewsTransportError):
    @override
    def __str__(self) -> str:
        return "Alpaca news fixture is invalid"


class _FixtureResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_index: int = Field(ge=0, lt=8)
    page_token: str | None
    received_at: dt.datetime
    http_status: int = Field(ge=100, le=599)
    content_type: str
    content_encoding: str = "identity"
    payload_path: str

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        path = Path(self.payload_path)
        if (
            not _aware(self.received_at)
            or not _valid_token(self.page_token)
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _ENCODING.fullmatch(self.content_encoding) is None
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise AlpacaNewsFixtureError
        return self


class _FixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    responses: tuple[_FixtureResponse, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        indexes = tuple(item.page_index for item in self.responses)
        received = tuple(item.received_at for item in self.responses)
        if (
            _HEX64.fullmatch(self.request_id) is None
            or indexes != tuple(range(len(self.responses)))
            or self.responses[0].page_token is not None
            or received != tuple(sorted(received))
        ):
            raise AlpacaNewsFixtureError
        return self


@dataclass(frozen=True, slots=True)
class _FixturePayload:
    response: _FixtureResponse
    raw_payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlpacaNewsFixtureFetcher:
    request_id: str
    payloads: tuple[_FixturePayload, ...] = field(repr=False)

    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        try:
            payload = self.payloads[page_index]
        except (IndexError, TypeError):
            raise AlpacaNewsFixtureError from None
        if (
            request.request_id != self.request_id
            or payload.response.page_index != page_index
            or payload.response.page_token != page_token
        ):
            raise AlpacaNewsFixtureError
        return AlpacaNewsRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=payload.response.received_at,
            status_code=payload.response.http_status,
            content_type=payload.response.content_type,
            content_encoding=payload.response.content_encoding,
            raw_payload=payload.raw_payload,
        )


def load_alpaca_news_fixture(path: Path) -> AlpacaNewsFixtureFetcher:
    try:
        manifest_path, payload = _read_file(path, 65_536)
        manifest = _FixtureManifest.model_validate_json(payload)
        pages = tuple(
            _FixturePayload(item, _read_payload(manifest_path, item.payload_path))
            for item in manifest.responses
        )
        return AlpacaNewsFixtureFetcher(manifest.request_id, pages)
    except AlpacaNewsFixtureError:
        raise
    except (OSError, TypeError, ValidationError, ValueError):
        raise AlpacaNewsFixtureError from None


def _read_payload(manifest: Path, relative_path: str) -> bytes:
    candidate = manifest.parent / relative_path
    if candidate.is_symlink():
        raise OSError
    resolved, payload = _read_file(candidate, ALPACA_NEWS_MAX_RAW_BYTES)
    if not resolved.is_relative_to(manifest.parent):
        raise OSError
    return payload


def _read_file(path: Path, maximum: int) -> tuple[Path, bytes]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise OSError
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, maximum + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        resolved = path.resolve(strict=True)
        current = resolved.stat()
        if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise OSError
    finally:
        os.close(descriptor)
    if len(payload) > maximum:
        raise OSError
    return resolved, bytes(payload)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _valid_token(value: str | None) -> bool:
    return value is None or (0 < len(value) <= 2_048 and not any(character < " " for character in value))


__all__ = (
    "AlpacaNewsFixtureError",
    "AlpacaNewsFixtureFetcher",
    "load_alpaca_news_fixture",
)
