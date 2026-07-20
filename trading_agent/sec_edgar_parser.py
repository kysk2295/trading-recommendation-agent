from __future__ import annotations

import datetime as dt
import io
import json
import zlib
from collections.abc import Iterator
from typing import Protocol

from ijson.backends import python as ijson_python
from ijson.common import JSONError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from trading_agent.sec_edgar_models import (
    SecEdgarResponseError,
    SecFilingEvent,
    SecSubmissionRawResponse,
    SecSubmissionSnapshot,
)

_MAX_RECENT_FILINGS = 2_000
_MAX_ADDITIONAL_HISTORY_FILES = 2_000
_MAX_DECODED_BYTES = 64 * 1024 * 1024
_ARRAY_ITEM_EVENTS = frozenset({"boolean", "null", "number", "start_array", "start_map", "string"})
_BOUNDED_ARRAY_ITEMS = frozenset(
    {
        "filings.files.item",
        "filings.recent.accessionNumber.item",
        "filings.recent.acceptanceDateTime.item",
        "filings.recent.filingDate.item",
        "filings.recent.form.item",
        "filings.recent.isInlineXBRL.item",
        "filings.recent.isXBRL.item",
        "filings.recent.items.item",
        "filings.recent.primaryDocDescription.item",
        "filings.recent.primaryDocument.item",
        "filings.recent.reportDate.item",
        "filings.recent.size.item",
    }
)


class _IjsonParse(Protocol):
    def __call__(self, source: io.BytesIO) -> Iterator[tuple[str, str, object]]: ...


_IJSON_PARSE: _IjsonParse = ijson_python.__dict__["parse"]


class _SecRecentFilings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    accession_number: tuple[StrictStr, ...] = Field(
        alias="accessionNumber", max_length=_MAX_RECENT_FILINGS
    )
    filing_date: tuple[StrictStr, ...] = Field(alias="filingDate", max_length=_MAX_RECENT_FILINGS)
    report_date: tuple[StrictStr, ...] = Field(alias="reportDate", max_length=_MAX_RECENT_FILINGS)
    acceptance_datetime: tuple[StrictStr, ...] = Field(
        alias="acceptanceDateTime", max_length=_MAX_RECENT_FILINGS
    )
    form: tuple[StrictStr, ...] = Field(max_length=_MAX_RECENT_FILINGS)
    items: tuple[StrictStr, ...] = Field(max_length=_MAX_RECENT_FILINGS)
    size: tuple[StrictInt, ...] = Field(max_length=_MAX_RECENT_FILINGS)
    is_xbrl: tuple[StrictInt, ...] = Field(alias="isXBRL", max_length=_MAX_RECENT_FILINGS)
    is_inline_xbrl: tuple[StrictInt, ...] = Field(
        alias="isInlineXBRL", max_length=_MAX_RECENT_FILINGS
    )
    primary_document: tuple[StrictStr, ...] = Field(
        alias="primaryDocument", max_length=_MAX_RECENT_FILINGS
    )
    primary_document_description: tuple[StrictStr, ...] = Field(
        alias="primaryDocDescription", max_length=_MAX_RECENT_FILINGS
    )


class _SecAdditionalFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _SecFilings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    recent: _SecRecentFilings
    files: tuple[_SecAdditionalFile, ...] = Field(max_length=_MAX_ADDITIONAL_HISTORY_FILES)


class _SecSubmissionDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    cik: StrictInt = Field(ge=0, le=9_999_999_999)
    filings: _SecFilings


def parse_sec_submission_snapshot(
    response: SecSubmissionRawResponse,
) -> SecSubmissionSnapshot:
    if response.status_code != 200:
        raise SecEdgarResponseError(f"http_{response.status_code}")
    if response.content_type != "application/json":
        raise SecEdgarResponseError("content_type")
    payload = _decoded_payload(response)
    _require_bounded_json_arrays(payload)
    try:
        document = _SecSubmissionDocument.model_validate_json(payload)
    except (UnicodeError, ValidationError, ValueError, json.JSONDecodeError):
        raise SecEdgarResponseError("response_structure") from None
    cik = f"{document.cik:010d}"
    if cik != response.cik:
        raise SecEdgarResponseError("cik_mismatch")
    recent = document.filings.recent
    columns = (
        recent.accession_number,
        recent.filing_date,
        recent.report_date,
        recent.acceptance_datetime,
        recent.form,
        recent.items,
        recent.size,
        recent.is_xbrl,
        recent.is_inline_xbrl,
        recent.primary_document,
        recent.primary_document_description,
    )
    row_count = len(recent.accession_number)
    if row_count > _MAX_RECENT_FILINGS or any(len(column) != row_count for column in columns):
        raise SecEdgarResponseError("column_lengths")
    filings = tuple(_filing(recent, index, cik, response.received_at) for index in range(row_count))
    return SecSubmissionSnapshot(
        cik=cik,
        filings=filings,
        additional_history_file_count=len(document.filings.files),
    )


def _require_bounded_json_arrays(payload: bytes) -> None:
    counts: dict[str, int] = {}
    try:
        for prefix, event, _value in _IJSON_PARSE(io.BytesIO(payload)):
            if prefix not in _BOUNDED_ARRAY_ITEMS or event not in _ARRAY_ITEM_EVENTS:
                continue
            count = counts.get(prefix, 0) + 1
            if count > _MAX_RECENT_FILINGS:
                raise SecEdgarResponseError("response_structure")
            counts[prefix] = count
    except SecEdgarResponseError:
        raise
    except (JSONError, UnicodeError, ValueError):
        raise SecEdgarResponseError("response_structure") from None


def _filing(
    recent: _SecRecentFilings,
    index: int,
    cik: str,
    received_at: dt.datetime,
) -> SecFilingEvent:
    try:
        filing_date = dt.date.fromisoformat(recent.filing_date[index])
        report_value = recent.report_date[index]
        report_date = None if report_value == "" else dt.date.fromisoformat(report_value)
        accepted_at = dt.datetime.fromisoformat(recent.acceptance_datetime[index].replace("Z", "+00:00"))
    except (IndexError, ValueError):
        raise SecEdgarResponseError("filing_time") from None
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise SecEdgarResponseError("acceptance_time")
    accepted_at = accepted_at.astimezone(dt.UTC)
    if accepted_at > received_at:
        raise SecEdgarResponseError("acceptance_time")
    is_xbrl = recent.is_xbrl[index]
    is_inline_xbrl = recent.is_inline_xbrl[index]
    if is_xbrl not in {0, 1} or is_inline_xbrl not in {0, 1}:
        raise SecEdgarResponseError("xbrl_flag")
    accession_number = recent.accession_number[index]
    if not accession_number.startswith(f"{cik}-"):
        raise SecEdgarResponseError("accession_cik_mismatch")
    items = tuple(item.strip() for item in recent.items[index].split(",") if item.strip())
    try:
        return SecFilingEvent(
            cik=cik,
            accession_number=accession_number,
            form=recent.form[index],
            filing_date=filing_date,
            report_date=report_date,
            accepted_at=accepted_at,
            primary_document=recent.primary_document[index],
            primary_document_description=recent.primary_document_description[index].strip(),
            items=items,
            size_bytes=recent.size[index],
            is_xbrl=bool(is_xbrl),
            is_inline_xbrl=bool(is_inline_xbrl),
        )
    except (SecEdgarResponseError, ValidationError, ValueError):
        raise SecEdgarResponseError("filing") from None


def _decoded_payload(response: SecSubmissionRawResponse) -> bytes:
    if response.content_encoding == "identity":
        return response.raw_payload
    if response.content_encoding not in {"gzip", "deflate"}:
        raise SecEdgarResponseError("content_encoding")
    window_bits = zlib.MAX_WBITS | 16 if response.content_encoding == "gzip" else zlib.MAX_WBITS
    try:
        decoder = zlib.decompressobj(window_bits)
        payload = decoder.decompress(response.raw_payload, _MAX_DECODED_BYTES + 1)
        if len(payload) > _MAX_DECODED_BYTES or decoder.unconsumed_tail:
            raise SecEdgarResponseError("decoded_response_too_large")
        payload += decoder.flush(_MAX_DECODED_BYTES + 1 - len(payload))
    except zlib.error:
        raise SecEdgarResponseError("content_encoding") from None
    if (
        len(payload) > _MAX_DECODED_BYTES
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise SecEdgarResponseError("content_encoding")
    return payload
