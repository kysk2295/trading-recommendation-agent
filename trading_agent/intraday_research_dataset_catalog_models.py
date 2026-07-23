from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from pydantic import BaseModel, ConfigDict

from trading_agent.intraday_research_dataset_models import IntradayResearchDatasetResult


@dataclass(frozen=True, slots=True)
class IntradayResearchDatasetCatalogRequest:
    session_dirs: tuple[Path, ...]
    output_root: Path
    minimum_sessions: int
    max_sessions: int
    max_bars: int
    producer_commit_sha: str
    required_session_dates: tuple[dt.date, ...] = ()


@dataclass(frozen=True, slots=True)
class IntradayResearchDatasetCatalogResult:
    dataset: IntradayResearchDatasetResult
    catalog_receipt_path: Path
    catalog_receipt_sha256: str
    candidate_sessions: int
    blocked_sessions: int
    created: bool


@dataclass(frozen=True, slots=True)
class IntradayResearchDatasetCatalogError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"intraday research dataset catalog blocked: {self.reason}"


class IntradayResearchDatasetSessionAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_name: str
    session_date: dt.date | None
    eligible: bool
    reason_codes: tuple[str, ...]


class IntradayResearchDatasetCatalogReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    dataset_input_sha256: str
    dataset_receipt_name: str
    minimum_sessions: int
    candidate_sessions: int
    required_session_dates: tuple[dt.date, ...]
    selected_session_dates: tuple[dt.date, ...]
    selected_source_sha256s: tuple[str, ...]
    audits: tuple[IntradayResearchDatasetSessionAudit, ...]


__all__ = (
    "IntradayResearchDatasetCatalogError",
    "IntradayResearchDatasetCatalogReceipt",
    "IntradayResearchDatasetCatalogRequest",
    "IntradayResearchDatasetCatalogResult",
    "IntradayResearchDatasetSessionAudit",
)
