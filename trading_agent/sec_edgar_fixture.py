from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.sec_edgar_client import MAX_SEC_SUBMISSION_BYTES, SecEdgarTransportError
from trading_agent.sec_edgar_models import SecSubmissionRawResponse, normalize_sec_cik

_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class SecEdgarFixtureError(SecEdgarTransportError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR fixture manifest or payload is invalid"


class _InvalidSecEdgarFixtureManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR fixture manifest is invalid"


class SecEdgarFixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    received_at: dt.datetime
    http_status: int
    content_type: str
    content_encoding: str = "identity"
    payload_path: str

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        path = Path(self.payload_path)
        if (
            self.received_at.tzinfo is None
            or self.received_at.utcoffset() is None
            or not 100 <= self.http_status <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", self.content_encoding) is None
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise _InvalidSecEdgarFixtureManifestError
        return self


@dataclass(frozen=True, slots=True)
class SecEdgarFixtureFetcher:
    manifest: SecEdgarFixtureManifest
    raw_payload: bytes = field(repr=False)

    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=normalize_sec_cik(cik),
            received_at=self.manifest.received_at,
            status_code=self.manifest.http_status,
            content_type=self.manifest.content_type,
            raw_payload=self.raw_payload,
            content_encoding=self.manifest.content_encoding,
        )


def load_sec_edgar_fixture(path: Path) -> SecEdgarFixtureFetcher:
    try:
        if path.is_symlink():
            raise OSError
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file() or manifest_path.stat().st_size > 65_536:
            raise OSError
        manifest = SecEdgarFixtureManifest.model_validate_json(manifest_path.read_bytes())
        candidate = manifest_path.parent / manifest.payload_path
        if candidate.is_symlink():
            raise OSError
        payload_path = candidate.resolve(strict=True)
        if not payload_path.is_relative_to(manifest_path.parent) or not payload_path.is_file():
            raise OSError
        payload = payload_path.read_bytes()
        if not payload or len(payload) > MAX_SEC_SUBMISSION_BYTES:
            raise OSError
        return SecEdgarFixtureFetcher(manifest, payload)
    except (OSError, ValidationError, ValueError):
        raise SecEdgarFixtureError from None
