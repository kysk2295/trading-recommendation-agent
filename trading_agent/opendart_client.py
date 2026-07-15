from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Self, final, override

import httpx2
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator

from trading_agent.opendart_config import (
    OPENDART_BASE_URL,
    OpenDartCredentials,
)

_SAFE_REQUEST_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_STATUS = re.compile(r"^[0-9]{3}$")
_CORP_CODE = re.compile(r"^[0-9]{8}$")
_STOCK_CODE = re.compile(r"^[0-9]{6}$")
_RECEIPT_NUMBER = re.compile(r"^[0-9]{14}$")
_RECEIPT_DATE = re.compile(r"^[0-9]{8}$")


class UnsafeOpenDartEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "OpenDART client endpoint는 공식 고정값이어야 합니다"


class UnsafeOpenDartRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "OpenDART client는 redirect를 따라가면 안 됩니다"


class OpenDartTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "OpenDART 전송에 실패했습니다"


class OpenDartResponseError(ValueError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    @override
    def __str__(self) -> str:
        return f"OpenDART 응답이 유효하지 않습니다: {self.failure_code}"


@dataclass(frozen=True, slots=True)
class OpenDartRawResponse:
    request_key: str
    requested_page: int
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            _SAFE_REQUEST_KEY.fullmatch(self.request_key) is None
            or not 1 <= self.requested_page <= 100
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or not self.raw_payload
        ):
            raise ValueError("invalid OpenDART raw response")


class OpenDartDisclosure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    corp_cls: Literal["Y", "K", "N", "E"]
    corp_name: StrictStr
    corp_code: StrictStr
    stock_code: StrictStr
    report_nm: StrictStr
    rcept_no: StrictStr
    flr_nm: StrictStr
    rcept_dt: StrictStr
    rm: StrictStr

    @model_validator(mode="after")
    def validate_disclosure(self) -> Self:
        if _RECEIPT_DATE.fullmatch(self.rcept_dt) is None:
            raise ValueError("invalid OpenDART disclosure")
        try:
            _ = dt.datetime.strptime(self.rcept_dt, "%Y%m%d")
        except ValueError:
            raise ValueError("invalid OpenDART disclosure") from None
        if (
            not _canonical_text(self.corp_name, max_length=300)
            or _CORP_CODE.fullmatch(self.corp_code) is None
            or (
                self.stock_code != ""
                and _STOCK_CODE.fullmatch(self.stock_code) is None
            )
            or not _canonical_text(self.report_nm, max_length=2_000)
            or _RECEIPT_NUMBER.fullmatch(self.rcept_no) is None
            or not _canonical_text(self.flr_nm, max_length=300)
            or not _bounded_text(self.rm, max_length=100)
        ):
            raise ValueError("invalid OpenDART disclosure")
        return self


class _OpenDartSuccessResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["000"]
    message: StrictStr
    page_no: StrictInt
    page_count: StrictInt
    total_count: StrictInt
    total_page: StrictInt
    disclosures: tuple[OpenDartDisclosure, ...] = Field(alias="list")


class _OpenDartStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StrictStr
    message: StrictStr


@dataclass(frozen=True, slots=True)
class OpenDartDisclosurePage:
    no_data: bool
    page_no: int
    page_count: int
    total_count: int
    total_page: int
    disclosures: tuple[OpenDartDisclosure, ...]


@final
class OpenDartClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: OpenDartCredentials,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != OPENDART_BASE_URL:
            raise UnsafeOpenDartEndpointError
        if client.follow_redirects:
            raise UnsafeOpenDartRedirectPolicyError
        self._client = client
        self._credentials = credentials
        self._clock = _clock

    def fetch_page(
        self,
        collection_date: dt.date,
        *,
        page_no: int,
    ) -> OpenDartRawResponse:
        if not 1 <= page_no <= 100:
            raise OpenDartTransportError
        date_value = collection_date.strftime("%Y%m%d")
        try:
            response = self._client.get(
                "/api/list.json",
                params={
                    "crtfc_key": self._credentials.api_key,
                    "bgn_de": date_value,
                    "end_de": date_value,
                    "sort": "date",
                    "sort_mth": "asc",
                    "page_no": str(page_no),
                    "page_count": "100",
                },
            )
            received_at = self._clock()
        except httpx2.HTTPError:
            raise OpenDartTransportError from None
        payload = bytes(response.content)
        if not payload or not _aware(received_at):
            raise OpenDartTransportError
        return OpenDartRawResponse(
            request_key=f"opendart:list:{date_value}:page:{page_no}",
            requested_page=page_no,
            received_at=received_at,
            status_code=response.status_code,
            content_type=_response_content_type(response),
            raw_payload=payload,
        )


def parse_opendart_disclosure_page(
    raw_response: OpenDartRawResponse,
) -> OpenDartDisclosurePage:
    if raw_response.status_code != httpx2.codes.OK:
        raise OpenDartResponseError(f"http_{raw_response.status_code}")
    if raw_response.content_type != "application/json":
        raise OpenDartResponseError("content_type")
    try:
        document: object = json.loads(raw_response.raw_payload)
    except (UnicodeError, json.JSONDecodeError):
        raise OpenDartResponseError("invalid_json") from None
    if not isinstance(document, dict):
        raise OpenDartResponseError("invalid_response")
    status = document.get("status")
    if status == "013":
        try:
            parsed_status = _OpenDartStatusResponse.model_validate(document)
        except ValidationError:
            raise OpenDartResponseError("invalid_response") from None
        if parsed_status.status != "013":
            raise OpenDartResponseError("invalid_response")
        return OpenDartDisclosurePage(
            no_data=True,
            page_no=raw_response.requested_page,
            page_count=100,
            total_count=0,
            total_page=0,
            disclosures=(),
        )
    if status != "000":
        failure_code = (
            f"opendart_{status}"
            if isinstance(status, str) and _STATUS.fullmatch(status) is not None
            else "invalid_response"
        )
        raise OpenDartResponseError(failure_code)
    try:
        parsed = _OpenDartSuccessResponse.model_validate(document)
    except ValidationError:
        raise OpenDartResponseError("invalid_response") from None
    expected_pages = (
        0
        if parsed.total_count == 0
        else (parsed.total_count + parsed.page_count - 1) // parsed.page_count
    )
    if (
        parsed.page_no != raw_response.requested_page
        or parsed.page_count != 100
        or parsed.total_count < 0
        or parsed.total_page != expected_pages
        or len(parsed.disclosures) > parsed.page_count
        or (parsed.total_count == 0 and parsed.disclosures)
        or (
            parsed.total_count > 0
            and not 1 <= parsed.page_no <= parsed.total_page
        )
    ):
        raise OpenDartResponseError("invalid_response")
    return OpenDartDisclosurePage(
        no_data=False,
        page_no=parsed.page_no,
        page_count=parsed.page_count,
        total_count=parsed.total_count,
        total_page=parsed.total_page,
        disclosures=parsed.disclosures,
    )


def _response_content_type(response: httpx2.Response) -> str:
    value = response.headers.get("content-type", "application/octet-stream")
    media_type = value.partition(";")[0].strip().lower()
    return (
        media_type
        if _CONTENT_TYPE.fullmatch(media_type) is not None
        else "application/octet-stream"
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return bool(value) and _bounded_text(value, max_length=max_length)


def _bounded_text(value: str, *, max_length: int) -> bool:
    return (
        value == value.strip()
        and len(value) <= max_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )
