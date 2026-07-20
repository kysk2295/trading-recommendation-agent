from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.sec_edgar_client import MAX_SEC_SUBMISSION_BYTES, SecEdgarTransportError
from trading_agent.sec_edgar_models import (
    SecSubmissionRawResponse,
    normalize_sec_cik,
    normalize_sec_history_file_name,
)

_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_HISTORY_FILE = re.compile(r"^CIK[0-9]{10}-submissions-[0-9]{3,6}\.json$")


class SecEdgarHistoryFixtureError(SecEdgarTransportError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR additional-history fixture is invalid"


class _FixtureResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file_name: str
    received_at: dt.datetime
    http_status: int
    content_type: str
    content_encoding: str = "identity"
    payload_path: str

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        path = Path(self.payload_path)
        if (
            _HISTORY_FILE.fullmatch(self.file_name) is None
            or self.received_at.tzinfo is None
            or self.received_at.utcoffset() is None
            or not 100 <= self.http_status <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", self.content_encoding) is None
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise SecEdgarHistoryFixtureError
        return self


class _FixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    responses: tuple[_FixtureResponse, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        names = tuple(item.file_name for item in self.responses)
        if len(names) != len(set(names)):
            raise SecEdgarHistoryFixtureError
        return self


@dataclass(frozen=True, slots=True)
class _FixturePayload:
    response: _FixtureResponse
    raw_payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecEdgarHistoryFixtureFetcher:
    payloads: tuple[_FixturePayload, ...]

    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse:
        try:
            cik = normalize_sec_cik(cik)
            file_name = normalize_sec_history_file_name(cik, file_name)
            payload = next(item for item in self.payloads if item.response.file_name == file_name)
        except (StopIteration, ValueError):
            raise SecEdgarHistoryFixtureError from None
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=payload.response.received_at,
            status_code=payload.response.http_status,
            content_type=payload.response.content_type,
            raw_payload=payload.raw_payload,
            content_encoding=payload.response.content_encoding,
        )


def load_sec_edgar_history_fixture(path: Path) -> SecEdgarHistoryFixtureFetcher:
    try:
        manifest_path, manifest_payload = _read_manifest(path)
        manifest = _FixtureManifest.model_validate_json(manifest_payload)
        payloads = tuple(
            _FixturePayload(item, _read_payload(manifest_path, item.payload_path))
            for item in manifest.responses
        )
        return SecEdgarHistoryFixtureFetcher(payloads)
    except (OSError, ValidationError, ValueError):
        raise SecEdgarHistoryFixtureError from None


def _read_manifest(path: Path) -> tuple[Path, bytes]:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65_536:
            raise OSError
        payload = bytearray()
        while len(payload) <= 65_536:
            chunk = os.read(descriptor, 65_537 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        manifest_path = path.resolve(strict=True)
        current = manifest_path.stat()
        if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise OSError
    finally:
        os.close(descriptor)
    if len(payload) > 65_536:
        raise OSError
    return manifest_path, bytes(payload)


def _read_payload(manifest_path: Path, relative_path: str) -> bytes:
    candidate = manifest_path.parent / relative_path
    if candidate.is_symlink():
        raise OSError
    payload_path = candidate.resolve(strict=True)
    if not payload_path.is_relative_to(manifest_path.parent) or not payload_path.is_file():
        raise OSError
    descriptor = os.open(
        payload_path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SEC_SUBMISSION_BYTES:
            raise OSError
        payload = bytearray()
        while len(payload) <= MAX_SEC_SUBMISSION_BYTES:
            chunk = os.read(descriptor, MAX_SEC_SUBMISSION_BYTES + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SEC_SUBMISSION_BYTES:
        raise OSError
    return bytes(payload)


__all__ = (
    "SecEdgarHistoryFixtureError",
    "SecEdgarHistoryFixtureFetcher",
    "load_sec_edgar_history_fixture",
)
