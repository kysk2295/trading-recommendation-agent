from __future__ import annotations

import datetime as dt
import json
import zlib

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from trading_agent.sec_edgar_models import (
    SecEdgarResponseError,
    SecFilingEvent,
    SecSubmissionRawResponse,
    SecSubmissionSnapshot,
)

_MAX_RECENT_FILINGS = 2_000
_MAX_DECODED_BYTES = 64 * 1024 * 1024


class _SecRecentFilings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    accession_number: tuple[StrictStr, ...] = Field(alias="accessionNumber")
    filing_date: tuple[StrictStr, ...] = Field(alias="filingDate")
    report_date: tuple[StrictStr, ...] = Field(alias="reportDate")
    acceptance_datetime: tuple[StrictStr, ...] = Field(alias="acceptanceDateTime")
    form: tuple[StrictStr, ...]
    items: tuple[StrictStr, ...]
    size: tuple[StrictInt, ...]
    is_xbrl: tuple[StrictInt, ...] = Field(alias="isXBRL")
    is_inline_xbrl: tuple[StrictInt, ...] = Field(alias="isInlineXBRL")
    primary_document: tuple[StrictStr, ...] = Field(alias="primaryDocument")
    primary_document_description: tuple[StrictStr, ...] = Field(alias="primaryDocDescription")


class _SecAdditionalFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    name: StrictStr
    filing_count: StrictInt = Field(alias="filingCount", ge=0)
    filing_from: StrictStr = Field(alias="filingFrom")
    filing_to: StrictStr = Field(alias="filingTo")


class _SecFilings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    recent: _SecRecentFilings
    files: tuple[_SecAdditionalFile, ...]


class _SecSubmissionDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    cik: StrictInt = Field(ge=0, le=9_999_999_999)
    name: StrictStr
    tickers: tuple[StrictStr, ...]
    exchanges: tuple[StrictStr, ...]
    filings: _SecFilings


def parse_sec_submission_snapshot(
    response: SecSubmissionRawResponse,
) -> SecSubmissionSnapshot:
    if response.status_code != 200:
        raise SecEdgarResponseError(f"http_{response.status_code}")
    if response.content_type != "application/json":
        raise SecEdgarResponseError("content_type")
    payload = _decoded_payload(response)
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
        entity_name=document.name,
        tickers=document.tickers,
        exchanges=document.exchanges,
        filings=filings,
        additional_history_file_count=len(document.filings.files),
    )


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
    items = tuple(item.strip() for item in recent.items[index].split(",") if item.strip())
    try:
        return SecFilingEvent(
            cik=cik,
            accession_number=recent.accession_number[index],
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
