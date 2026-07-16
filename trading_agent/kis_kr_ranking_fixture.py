from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kis_kr_ranking import (
    MAX_ATTEMPTS,
    MAX_PAGES_PER_KIND,
    KisKrRankingKind,
    KisKrRankingRawResponse,
    KisKrRankingTransportError,
)

_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_REQUEST_TR_CONT = frozenset({"", "N"})
_RESPONSE_TR_CONT = frozenset({"", "M", "F"})
_KST = ZoneInfo("Asia/Seoul")


class KisKrRankingFixtureError(KisKrRankingTransportError):
    @override
    def __str__(self) -> str:
        return "KIS KR ranking fixture manifest 또는 raw page가 유효하지 않습니다"


class KisKrRankingFixturePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    kind: KisKrRankingKind
    page_no: int
    attempt: int
    request_tr_cont: str
    response_tr_cont: str
    received_at: dt.datetime
    http_status: int
    content_type: str
    payload_path: str

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        path = Path(self.payload_path)
        if (
            not 1 <= self.page_no <= MAX_PAGES_PER_KIND
            or not 1 <= self.attempt <= MAX_ATTEMPTS
            or self.request_tr_cont not in _REQUEST_TR_CONT
            or self.response_tr_cont not in _RESPONSE_TR_CONT
            or not _aware(self.received_at)
            or not 100 <= self.http_status <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("invalid KIS KR ranking fixture page")
        return self


class KisKrRankingFixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_date: dt.date
    pages: tuple[KisKrRankingFixturePage, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if not self.pages or isinstance(self.collection_date, dt.datetime):
            raise ValueError("invalid KIS KR ranking fixture manifest")
        identities = tuple(
            (
                item.kind,
                item.page_no,
                item.attempt,
                item.request_tr_cont,
            )
            for item in self.pages
        )
        if len(identities) != len(set(identities)):
            raise ValueError("invalid KIS KR ranking fixture manifest")
        by_kind: dict[KisKrRankingKind, list[int]] = {}
        for item in self.pages:
            by_kind.setdefault(item.kind, []).append(item.page_no)
        if set(by_kind) != set(KisKrRankingKind):
            raise ValueError("invalid KIS KR ranking fixture manifest")
        for page_numbers in by_kind.values():
            unique_pages = sorted(set(page_numbers))
            if unique_pages != list(range(1, len(unique_pages) + 1)):
                raise ValueError("invalid KIS KR ranking fixture manifest")
        return self


@dataclass(frozen=True, slots=True)
class _FixtureCall:
    kind: KisKrRankingKind
    page_no: int
    attempt: int
    tr_cont: str
    response: KisKrRankingRawResponse = field(repr=False)


@dataclass(slots=True)
class KisKrRankingFixtureFetcher:
    collection_date: dt.date
    _calls: tuple[_FixtureCall, ...] = field(repr=False)
    _index: int = field(default=0, repr=False)

    def fetch_page(
        self,
        kind: KisKrRankingKind,
        *,
        page_no: int,
        attempt: int,
        tr_cont: str,
    ) -> KisKrRankingRawResponse:
        if self._index >= len(self._calls):
            raise KisKrRankingFixtureError
        expected = self._calls[self._index]
        if (
            expected.kind is not kind
            or expected.page_no != page_no
            or expected.attempt != attempt
            or expected.tr_cont != tr_cont
        ):
            raise KisKrRankingFixtureError
        self._index += 1
        return expected.response


def load_kis_kr_ranking_fixture(
    path: Path,
    *,
    collection_date: dt.date,
) -> KisKrRankingFixtureFetcher:
    try:
        if path.is_symlink():
            raise OSError
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file():
            raise OSError
        manifest = KisKrRankingFixtureManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        if manifest.collection_date != collection_date:
            raise ValueError("collection date mismatch")
        base = manifest_path.parent
        calls: list[_FixtureCall] = []
        for item in manifest.pages:
            if item.received_at.astimezone(_KST).date() != collection_date:
                raise ValueError("received_at date mismatch")
            candidate = base / item.payload_path
            if candidate.is_symlink():
                raise OSError
            payload_path = candidate.resolve(strict=True)
            if not payload_path.is_relative_to(base) or not payload_path.is_file():
                raise OSError
            payload = payload_path.read_bytes()
            if not payload:
                raise OSError
            request_key = (
                f"kis-kr:{item.kind.value}:p{item.page_no}:a{item.attempt}:"
                f"rq-{item.request_tr_cont.lower()}:"
                f"rs-{item.response_tr_cont.lower()}"
            )
            response = KisKrRankingRawResponse(
                kind=item.kind,
                page_no=item.page_no,
                attempt=item.attempt,
                request_tr_cont=item.request_tr_cont,
                response_tr_cont=item.response_tr_cont,
                request_key=request_key,
                received_at=item.received_at,
                status_code=item.http_status,
                content_type=item.content_type,
                raw_payload=payload,
            )
            calls.append(
                _FixtureCall(
                    kind=item.kind,
                    page_no=item.page_no,
                    attempt=item.attempt,
                    tr_cont=item.request_tr_cont,
                    response=response,
                )
            )
        return KisKrRankingFixtureFetcher(collection_date, tuple(calls))
    except (OSError, ValidationError, ValueError):
        raise KisKrRankingFixtureError from None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
