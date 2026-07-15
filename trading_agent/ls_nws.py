from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, Self, override
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictStr,
    ValidationError,
    model_validator,
)

MAX_LS_NWS_FRAME_BYTES: Final = 262_144
_KST: Final = ZoneInfo("Asia/Seoul")
_REALKEY = re.compile(r"^[0-9]{24}$")
_UNSIGNED_DECIMAL = re.compile(r"^[0-9]{1,10}$")
_DATE = re.compile(r"^[0-9]{8}$")
_TIME = re.compile(r"^[0-9]{6}$")


class LsNwsWireKind(StrEnum):
    TEXT = "text"
    BINARY = "binary"


@dataclass(frozen=True, slots=True)
class LsNwsRawFrame:
    sequence: int
    received_at: dt.datetime
    wire_kind: LsNwsWireKind
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not 1 <= self.sequence <= 999_999
            or not _aware(self.received_at)
            or not isinstance(self.wire_kind, LsNwsWireKind)
            or not self.raw_payload
            or len(self.raw_payload) > MAX_LS_NWS_FRAME_BYTES
        ):
            raise ValueError("invalid LS NWS raw frame")


@dataclass(frozen=True, slots=True)
class ParsedLsNwsNews:
    realkey: str
    source_record_id: str
    published_at: dt.datetime
    canonical_payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ParsedLsNwsSubscriptionAck:
    received_at: dt.datetime


class LsNwsParseError(ValueError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    @override
    def __str__(self) -> str:
        return f"LS NWS frame이 유효하지 않습니다: {self.failure_code}"


class _DuplicateJsonKeyError(ValueError):
    pass


class _LsNwsHeader(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    tr_cd: Literal["NWS"]
    tr_key: Literal["NWS001"]


class _LsNwsBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    date: StrictStr
    code: StrictStr
    realkey: StrictStr
    bodysize: StrictStr
    time: StrictStr
    id: StrictStr
    title: StrictStr
    categoryid: StrictStr | None = None
    codeaccu: StrictStr | None = None

    @model_validator(mode="after")
    def validate_body(self) -> Self:
        extension_fields = {"categoryid", "codeaccu"}.intersection(
            self.model_fields_set
        )
        if (
            not _valid_date(self.date)
            or not _valid_time(self.time)
            or _REALKEY.fullmatch(self.realkey) is None
            or _UNSIGNED_DECIMAL.fullmatch(self.bodysize) is None
            or _UNSIGNED_DECIMAL.fullmatch(self.id) is None
            or not _valid_code(self.code)
            or not _valid_title(self.title)
            or extension_fields not in (set(), {"categoryid", "codeaccu"})
            or (
                extension_fields
                and (
                    self.categoryid is None
                    or self.codeaccu is None
                    or _UNSIGNED_DECIMAL.fullmatch(self.categoryid) is None
                    or not _valid_opaque_extension(self.codeaccu)
                )
            )
        ):
            raise ValueError("invalid LS NWS body")
        return self


class _LsNwsPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    header: _LsNwsHeader
    body: _LsNwsBody


class _LsNwsAckHeader(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    rsp_cd: StrictStr
    rsp_msg: StrictStr
    tr_cd: Literal["NWS"]
    tr_type: Literal["3"]

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if not _valid_control_message(self.rsp_msg):
            raise ValueError("invalid LS NWS acknowledgement message")
        return self


class _LsNwsAckPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    header: _LsNwsAckHeader
    body: None


type ParsedLsNwsPacket = ParsedLsNwsSubscriptionAck | ParsedLsNwsNews


def parse_ls_nws_packet(
    frame: LsNwsRawFrame,
    *,
    collection_date: dt.date,
) -> ParsedLsNwsPacket:
    try:
        text = frame.raw_payload.decode("utf-8")
    except UnicodeDecodeError:
        raise LsNwsParseError("invalid_utf8") from None
    try:
        document: object = json.loads(text, object_pairs_hook=_unique_json_object)
    except _DuplicateJsonKeyError:
        raise LsNwsParseError("duplicate_json_key") from None
    except json.JSONDecodeError:
        raise LsNwsParseError("invalid_json") from None
    if _looks_like_control(document):
        try:
            packet = _LsNwsAckPacket.model_validate(document)
        except ValidationError:
            raise LsNwsParseError("invalid_control_packet") from None
        if packet.header.rsp_cd != "00000":
            raise LsNwsParseError("subscription_rejected")
        return ParsedLsNwsSubscriptionAck(received_at=frame.received_at)
    return parse_ls_nws_frame(frame, collection_date=collection_date)


def _looks_like_control(document: object) -> bool:
    if not isinstance(document, dict):
        return False
    header = document.get("header")
    return isinstance(header, dict) and any(
        key in header for key in ("rsp_cd", "rsp_msg", "tr_type")
    )


def parse_ls_nws_frame(
    frame: LsNwsRawFrame,
    *,
    collection_date: dt.date,
) -> ParsedLsNwsNews:
    try:
        text = frame.raw_payload.decode("utf-8")
    except UnicodeDecodeError:
        raise LsNwsParseError("invalid_utf8") from None
    try:
        document: object = json.loads(text, object_pairs_hook=_unique_json_object)
    except _DuplicateJsonKeyError:
        raise LsNwsParseError("duplicate_json_key") from None
    except json.JSONDecodeError:
        raise LsNwsParseError("invalid_json") from None
    try:
        packet = _LsNwsPacket.model_validate(document)
    except ValidationError:
        raise LsNwsParseError("invalid_packet") from None
    if packet.body.date != collection_date.strftime("%Y%m%d"):
        raise LsNwsParseError("collection_date_mismatch")
    published_at = dt.datetime.strptime(
        packet.body.date + packet.body.time,
        "%Y%m%d%H%M%S",
    ).replace(tzinfo=_KST)
    if published_at > frame.received_at:
        raise LsNwsParseError("future_publication")
    canonical_document = {
        "tr_cd": packet.header.tr_cd,
        "tr_key": packet.header.tr_key,
        "date": packet.body.date,
        "code": packet.body.code,
        "realkey": packet.body.realkey,
        "bodysize": packet.body.bodysize,
        "time": packet.body.time,
        "id": packet.body.id,
        "title": packet.body.title,
    }
    if packet.body.categoryid is not None and packet.body.codeaccu is not None:
        canonical_document.update(
            {
                "categoryid": packet.body.categoryid,
                "codeaccu": packet.body.codeaccu,
            }
        )
    canonical_payload = json.dumps(
        canonical_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ParsedLsNwsNews(
        realkey=packet.body.realkey,
        source_record_id=f"ls-nws://news/{packet.body.realkey}",
        published_at=published_at,
        canonical_payload=canonical_payload,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _valid_date(value: str) -> bool:
    if _DATE.fullmatch(value) is None:
        return False
    try:
        _ = dt.datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _valid_time(value: str) -> bool:
    if _TIME.fullmatch(value) is None:
        return False
    try:
        _ = dt.datetime.strptime(value, "%H%M%S")
    except ValueError:
        return False
    return True


def _valid_code(value: str) -> bool:
    return value == "" or (
        len(value) <= 32
        and value.isascii()
        and all(33 <= ord(character) <= 126 for character in value)
    )


def _valid_title(value: str) -> bool:
    return (
        value == value.strip()
        and 1 <= len(value) <= 2_000
        and not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    )


def _valid_control_message(value: str) -> bool:
    return (
        value == value.strip()
        and 1 <= len(value) <= 200
        and not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    )


def _valid_opaque_extension(value: str) -> bool:
    return len(value) <= 256 and not any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
