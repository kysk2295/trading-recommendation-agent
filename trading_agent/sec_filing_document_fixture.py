from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.sec_filing_document_client import (
    MAX_SEC_FILING_DOCUMENT_BYTES,
    SecFilingDocumentTransportError,
)
from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentTarget,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class SecFilingDocumentFixtureError(SecFilingDocumentTransportError):
    @override
    def __str__(self) -> str:
        return "SEC filing document fixture is invalid"


class _FixtureResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    received_at: dt.datetime
    http_status: int
    content_type: str
    content_encoding: str = "identity"
    payload_path: str

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        path = Path(self.payload_path)
        if (
            _HEX64.fullmatch(self.target_id) is None
            or self.received_at.tzinfo is None
            or self.received_at.utcoffset() is None
            or not 100 <= self.http_status <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", self.content_encoding) is None
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise SecFilingDocumentFixtureError
        return self


class _FixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    responses: tuple[_FixtureResponse, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        identities = tuple(item.target_id for item in self.responses)
        if len(identities) != len(set(identities)):
            raise SecFilingDocumentFixtureError
        return self


@dataclass(frozen=True, slots=True)
class _FixturePayload:
    response: _FixtureResponse
    raw_payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecFilingDocumentFixtureFetcher:
    payloads: tuple[_FixturePayload, ...]

    def fetch(self, target: SecFilingDocumentTarget) -> SecFilingDocumentRawResponse:
        try:
            payload = next(item for item in self.payloads if item.response.target_id == target.target_id)
        except StopIteration:
            raise SecFilingDocumentFixtureError from None
        return SecFilingDocumentRawResponse(
            target_id=target.target_id,
            received_at=payload.response.received_at,
            status_code=payload.response.http_status,
            content_type=payload.response.content_type,
            content_encoding=payload.response.content_encoding,
            raw_payload=payload.raw_payload,
        )


def load_sec_filing_document_fixture(path: Path) -> SecFilingDocumentFixtureFetcher:
    try:
        manifest_path, payload = _read_file(path, 65_536)
        manifest = _FixtureManifest.model_validate_json(payload)
        responses = tuple(
            _FixturePayload(
                item,
                _read_relative(manifest_path, item.payload_path),
            )
            for item in manifest.responses
        )
        return SecFilingDocumentFixtureFetcher(responses)
    except (OSError, ValidationError, ValueError):
        raise SecFilingDocumentFixtureError from None


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


def _read_relative(manifest: Path, relative: str) -> bytes:
    candidate = manifest.parent / relative
    if candidate.is_symlink():
        raise OSError
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(manifest.parent):
        raise OSError
    _, payload = _read_file(resolved, MAX_SEC_FILING_DOCUMENT_BYTES)
    return payload


__all__ = (
    "SecFilingDocumentFixtureError",
    "SecFilingDocumentFixtureFetcher",
    "load_sec_filing_document_fixture",
)
