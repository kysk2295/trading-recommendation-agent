from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import override

from trading_agent.sec_edgar_models import SecFilingEvent, SecSubmissionRawResponse, SecSubmissionRun


class InvalidSecEdgarStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR store is invalid"


@dataclass(frozen=True, slots=True)
class SecStoredReceipt:
    response: SecSubmissionRawResponse = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecStoredFilingVersion:
    version_id: str
    event: SecFilingEvent
    previous_version_id: str | None
    receipt_id: str
    observed_at: dt.datetime
    item_index: int


@dataclass(frozen=True, slots=True)
class SecReceiptAppendResult:
    stored: SecStoredReceipt
    created: bool


@dataclass(frozen=True, slots=True)
class SecCollectionAppendResult:
    run: SecSubmissionRun
    filings: tuple[SecStoredFilingVersion, ...]
    created: bool
    new_filing_version_count: int
