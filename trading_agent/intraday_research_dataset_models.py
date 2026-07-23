from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

_COMMIT_SHA: Final = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class IntradayResearchDatasetRequest:
    session_dirs: tuple[Path, ...]
    output_root: Path
    max_sessions: int
    max_bars: int
    producer_commit_sha: str


@dataclass(frozen=True, slots=True)
class IntradayResearchDatasetResult:
    csv_path: Path
    receipt_path: Path
    input_sha256: str
    source_session_sha256s: tuple[str, ...]
    session_count: int
    eligible_symbol_sessions: int
    censored_symbol_sessions: int
    bar_count: int
    created: bool


@dataclass(frozen=True, slots=True)
class IntradayResearchDatasetError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"intraday research dataset blocked: {self.reason}"


class IntradayResearchDatasetReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    producer_commit_sha: str
    input_sha256: str
    source_session_sha256s: tuple[str, ...]
    session_dates: tuple[dt.date, ...]
    eligible_symbol_sessions: int
    censored_symbol_sessions: int
    bar_count: int

    @model_validator(mode="after")
    def validate_producer(self) -> Self:
        if _COMMIT_SHA.fullmatch(self.producer_commit_sha) is None:
            raise IntradayResearchDatasetError("invalid_producer_commit")
        return self


__all__ = (
    "IntradayResearchDatasetError",
    "IntradayResearchDatasetReceipt",
    "IntradayResearchDatasetRequest",
    "IntradayResearchDatasetResult",
)
