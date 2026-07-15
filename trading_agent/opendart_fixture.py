from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.opendart_client import (
    OpenDartRawResponse,
    OpenDartTransportError,
)

_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class OpenDartFixtureError(OpenDartTransportError):
    @override
    def __str__(self) -> str:
        return "OpenDART fixture manifest 또는 raw page가 유효하지 않습니다"


class OpenDartFixturePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    page_no: int
    received_at: dt.datetime
    http_status: int
    content_type: str
    payload_path: str

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        path = Path(self.payload_path)
        if (
            not 1 <= self.page_no <= 100
            or not _aware(self.received_at)
            or not 100 <= self.http_status <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("invalid OpenDART fixture page")
        return self


class OpenDartFixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    pages: tuple[OpenDartFixturePage, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        page_numbers = tuple(item.page_no for item in self.pages)
        payload_paths = tuple(item.payload_path for item in self.pages)
        if (
            not self.pages
            or page_numbers != tuple(range(1, len(self.pages) + 1))
            or len(payload_paths) != len(set(payload_paths))
        ):
            raise ValueError("invalid OpenDART fixture manifest")
        return self


@dataclass(frozen=True, slots=True)
class OpenDartFixtureFetcher:
    collection_date: dt.date
    pages: tuple[OpenDartRawResponse, ...] = field(repr=False)

    def fetch_page(
        self,
        collection_date: dt.date,
        *,
        page_no: int,
    ) -> OpenDartRawResponse:
        if collection_date != self.collection_date or not 1 <= page_no <= len(self.pages):
            raise OpenDartFixtureError
        return self.pages[page_no - 1]


def load_opendart_fixture(
    path: Path,
    *,
    collection_date: dt.date,
) -> OpenDartFixtureFetcher:
    try:
        if path.is_symlink():
            raise OSError
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file():
            raise OSError
        manifest = OpenDartFixtureManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        base = manifest_path.parent
        date_value = collection_date.strftime("%Y%m%d")
        pages: list[OpenDartRawResponse] = []
        for item in manifest.pages:
            candidate = base / item.payload_path
            if candidate.is_symlink():
                raise OSError
            payload_path = candidate.resolve(strict=True)
            if not payload_path.is_relative_to(base) or not payload_path.is_file():
                raise OSError
            payload = payload_path.read_bytes()
            if not payload:
                raise OSError
            pages.append(
                OpenDartRawResponse(
                    request_key=(
                        f"opendart:list:{date_value}:page:{item.page_no}"
                    ),
                    requested_page=item.page_no,
                    received_at=item.received_at,
                    status_code=item.http_status,
                    content_type=item.content_type,
                    raw_payload=payload,
                )
            )
        return OpenDartFixtureFetcher(collection_date, tuple(pages))
    except (OSError, ValidationError, ValueError):
        raise OpenDartFixtureError from None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
