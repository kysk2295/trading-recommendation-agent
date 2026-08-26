from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address
from typing import Annotated, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

MAX_RESPONSE_BYTES: Final = 16 * 1024
_REQUEST_ID: Final = StringConstraints(pattern=r"[0-9a-f]{64}", min_length=64, max_length=64)
_TARGET_ID: Final = StringConstraints(min_length=1, max_length=256)
_QUERY: Final = StringConstraints(min_length=1, max_length=500)
RequestId = Annotated[str, _REQUEST_ID]
TargetId = Annotated[str, _TARGET_ID]
Query = Annotated[str, _QUERY]


class BrowserAction(StrEnum):
    STATUS = "status"
    SEARCH = "search"
    OPEN = "open"
    READ = "read"
    FOLLOW = "follow"
    CAPTURE = "capture"


class BrowserFailureReason(StrEnum):
    URL_NOT_PUBLIC_HTTPS = "browser_url_not_public_https"
    VISIBLE_TEXT_UNAVAILABLE = "browser_visible_text_unavailable"
    NAVIGATION_BLOCKED = "browser_navigation_blocked"
    CDP_TIMEOUT = "browser_cdp_timeout"
    RESPONSE_TOO_LARGE = "browser_response_too_large"


@dataclass(frozen=True, slots=True)
class InvalidLocalBrowserProtocolError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


def _protocol_error() -> InvalidLocalBrowserProtocolError:
    return InvalidLocalBrowserProtocolError(reason=BrowserFailureReason.URL_NOT_PUBLIC_HTTPS.value)


def _is_valid_hostname(hostname: str) -> bool:
    if len(hostname.rstrip(".")) > 253:
        return False
    labels = hostname.rstrip(".").split(".")
    return bool(labels and all(
        label
        and len(label) <= 63
        and label[0] != "-"
        and label[-1] != "-"
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ))


def require_public_https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or parsed.username is not None or parsed.password is not None:
            raise _protocol_error() from None
        hostname = parsed.hostname
        port = parsed.port
    except (ValueError, InvalidLocalBrowserProtocolError) as error:
        if isinstance(error, InvalidLocalBrowserProtocolError):
            raise
        raise _protocol_error() from None

    if hostname is None or not hostname or any(character.isspace() or ord(character) < 32 for character in hostname):
        raise _protocol_error()

    try:
        address = ip_address(hostname)
    except ValueError:
        labels = hostname.rstrip(".").split(".")
        if not _is_valid_hostname(hostname) or all(label.isdigit() for label in labels):
            raise _protocol_error() from None
    else:
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_private
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise _protocol_error() from None

    if port not in (None, 443):
        raise _protocol_error()
    normalized_host = hostname.lower()
    netloc = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    return urlunsplit(("https", netloc, parsed.path, parsed.query, ""))


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class BrowserStatusRequest(_StrictModel):
    request_id: RequestId


class BrowserSearchRequest(_StrictModel):
    request_id: RequestId
    query: Query


class BrowserOpenRequest(_StrictModel):
    request_id: RequestId
    url: str = Field(min_length=8, max_length=2_048)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return require_public_https_url(value)


class BrowserReadRequest(_StrictModel):
    request_id: RequestId
    target_id: TargetId


class BrowserFollowRequest(_StrictModel):
    request_id: RequestId
    target_id: TargetId
    link_index: int = Field(ge=0, le=99)


class BrowserCaptureRequest(_StrictModel):
    request_id: RequestId
    target_id: TargetId


class BrowserVisibleLink(_StrictModel):
    label: str = Field(default="", max_length=200)
    url: str = Field(min_length=8, max_length=2_048)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return require_public_https_url(value)


class BrowserPageObservation(_StrictModel):
    target_id: TargetId
    url: str = Field(min_length=8, max_length=2_048)
    title: str = Field(default="", max_length=500)
    visible_text: str = Field(default="", max_length=12_000)
    links: tuple[BrowserVisibleLink, ...] = Field(default=(), max_length=40)
    captured_at: AwareDatetime

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return require_public_https_url(value)

    @field_validator("captured_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class BrowserSearchResult(_StrictModel):
    title: str = Field(default="", max_length=500)
    url: str = Field(min_length=8, max_length=2_048)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return require_public_https_url(value)


class BrowserScreenshotReceipt(_StrictModel):
    path: str = Field(min_length=1, max_length=2_048)
    sha256: str = Field(pattern=r"[0-9a-f]{64}", min_length=64, max_length=64)
    width: int | None = Field(default=None, ge=1, le=20_000)
    height: int | None = Field(default=None, ge=1, le=20_000)
    captured_at: AwareDatetime

    @field_validator("captured_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class BrowserFailure(_StrictModel):
    reason: BrowserFailureReason


class BrowserResponse(_StrictModel):
    request_id: RequestId
    action: BrowserAction
    status: Literal["ok", "error"] = "ok"
    observation: BrowserPageObservation | None = None
    search_results: tuple[BrowserSearchResult, ...] = Field(default=(), max_length=40)
    screenshot: BrowserScreenshotReceipt | None = None
    failure: BrowserFailure | None = None

    @model_validator(mode="after")
    def enforce_canonical_size(self) -> "BrowserResponse":
        if len(self.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise PydanticCustomError("browser_response_too_large", "browser response exceeds 16 KiB")
        return self
