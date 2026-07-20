from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, override

from trading_agent.sec_edgar_collection import SecCollectionResult
from trading_agent.sec_edgar_models import SecSubmissionRawResponse

MAX_SEC_HISTORY_FILES_PER_COLLECTION: Final = 8


class SecAdditionalHistoryFetcher(Protocol):
    def fetch_additional_history(
        self,
        collection_id: str,
        cik: str,
        file_name: str,
    ) -> SecSubmissionRawResponse: ...


class InvalidSecEdgarHistoryCollectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR additional-history collection state is invalid"


@dataclass(frozen=True, slots=True)
class SecAdditionalHistoryCollectionResult:
    parent_run_id: str
    discovered_file_count: int
    selected_file_count: int
    completed_file_count: int
    filing_count: int
    new_filing_version_count: int
    replayed_file_count: int
    files: tuple[SecCollectionResult, ...]


__all__ = (
    "MAX_SEC_HISTORY_FILES_PER_COLLECTION",
    "InvalidSecEdgarHistoryCollectionError",
    "SecAdditionalHistoryCollectionResult",
    "SecAdditionalHistoryFetcher",
)
