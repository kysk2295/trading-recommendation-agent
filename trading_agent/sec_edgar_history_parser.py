from __future__ import annotations

import json

from pydantic import ValidationError

from trading_agent.sec_edgar_models import (
    SecAdditionalHistoryFile,
    SecEdgarResponseError,
    SecSubmissionRawResponse,
    SecSubmissionSnapshot,
)
from trading_agent.sec_edgar_parser import (
    SecRecentFilingsColumns,
    decoded_sec_payload,
    filings_from_sec_columns,
    require_bounded_sec_json_arrays,
)


def parse_sec_additional_history_snapshot(
    response: SecSubmissionRawResponse,
    manifest: SecAdditionalHistoryFile,
) -> SecSubmissionSnapshot:
    if response.status_code != 200:
        raise SecEdgarResponseError(f"http_{response.status_code}")
    if response.content_type != "application/json":
        raise SecEdgarResponseError("content_type")
    payload = decoded_sec_payload(response)
    require_bounded_sec_json_arrays(payload)
    try:
        recent = SecRecentFilingsColumns.model_validate_json(payload)
    except (UnicodeError, ValidationError, ValueError, json.JSONDecodeError):
        raise SecEdgarResponseError("response_structure") from None
    filings = filings_from_sec_columns(recent, response.cik, response.received_at)
    if (
        manifest.cik != response.cik
        or len(filings) != manifest.filing_count
        or any(
            item.filing_date < manifest.filing_from or item.filing_date > manifest.filing_to
            for item in filings
        )
    ):
        raise SecEdgarResponseError("history_manifest")
    return SecSubmissionSnapshot(
        cik=response.cik,
        filings=filings,
        additional_history_files=(),
    )


__all__ = ("parse_sec_additional_history_snapshot",)
