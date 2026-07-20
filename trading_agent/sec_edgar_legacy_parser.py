from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from trading_agent.sec_edgar_models import (
    SecEdgarResponseError,
    SecFilingEvent,
    SecSubmissionRawResponse,
)
from trading_agent.sec_edgar_parser import (
    SecRecentFilingsColumns,
    decoded_sec_payload,
    filings_from_sec_columns,
    require_bounded_sec_json_arrays,
)


class _LegacyAdditionalFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _LegacyFilings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    recent: SecRecentFilingsColumns
    files: tuple[_LegacyAdditionalFile, ...] = Field(max_length=2_000)


class _LegacySubmissionDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    cik: StrictInt = Field(ge=0, le=9_999_999_999)
    filings: _LegacyFilings


@dataclass(frozen=True, slots=True)
class SecLegacySubmissionProjection:
    filings: tuple[SecFilingEvent, ...]
    additional_history_file_count: int


def parse_legacy_sec_submission_projection(
    response: SecSubmissionRawResponse,
) -> SecLegacySubmissionProjection:
    if response.status_code != 200:
        raise SecEdgarResponseError(f"http_{response.status_code}")
    if response.content_type != "application/json":
        raise SecEdgarResponseError("content_type")
    payload = decoded_sec_payload(response)
    require_bounded_sec_json_arrays(payload)
    try:
        document = _LegacySubmissionDocument.model_validate_json(payload)
    except (UnicodeError, ValidationError, ValueError):
        raise SecEdgarResponseError("response_structure") from None
    cik = f"{document.cik:010d}"
    if cik != response.cik:
        raise SecEdgarResponseError("cik_mismatch")
    return SecLegacySubmissionProjection(
        filings=filings_from_sec_columns(document.filings.recent, cik, response.received_at),
        additional_history_file_count=len(document.filings.files),
    )


__all__ = ("parse_legacy_sec_submission_projection",)
