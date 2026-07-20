from __future__ import annotations

from collections.abc import Sequence

from trading_agent.sec_edgar_models import (
    SecEdgarResponseError,
    SecFilingEvent,
    SecSubmissionRawResponse,
    SecSubmissionRun,
)
from trading_agent.sec_edgar_parser import parse_sec_submission_snapshot
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError


def require_receipt_projection(
    response: SecSubmissionRawResponse,
    run: SecSubmissionRun,
    events: Sequence[SecFilingEvent],
) -> None:
    try:
        expected = parse_sec_submission_snapshot(response)
    except SecEdgarResponseError:
        raise InvalidSecEdgarStoreError from None
    if (
        response.collection_id != run.collection_id
        or response.cik != run.cik
        or response.receipt_id != run.receipt_id
        or expected.filings != tuple(events)
        or expected.additional_history_file_count != run.additional_history_file_count
    ):
        raise InvalidSecEdgarStoreError
