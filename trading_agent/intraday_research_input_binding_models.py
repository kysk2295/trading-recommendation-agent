from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from pydantic import BaseModel, ConfigDict

from trading_agent.strategy_factory import StrategyMode


@dataclass(frozen=True, slots=True)
class IntradayResearchStrategyBinding:
    strategy: StrategyMode
    strategy_version: str
    queue_card_key: str


@dataclass(frozen=True, slots=True)
class IntradayResearchInputBindingRequest:
    dataset_csv: Path
    dataset_receipt: Path
    entitlement_contract: Path
    source_queue_artifact: Path
    output_root: Path
    strategy_bindings: tuple[IntradayResearchStrategyBinding, ...]
    code_version: str
    registered_at: dt.datetime
    observed_at: dt.datetime
    minimum_training_sessions: int
    max_bars: int
    max_sessions: int
    per_side_fee_bps: int
    per_side_slippage_bps: int
    bootstrap_samples: int
    rss_limit_gib: float


@dataclass(frozen=True, slots=True)
class IntradayResearchInputBindingResult:
    input_sha256: str
    foundation_paths: tuple[Path, ...]
    foundation_sha256s: tuple[str, ...]
    manifest_path: Path
    manifest_sha256: str
    receipt_path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class IntradayResearchInputBindingError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"intraday research input binding blocked: {self.reason}"


class IntradayResearchInputBindingReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    input_sha256: str
    dataset_receipt_sha256: str
    entitlement_contract_sha256: str
    source_queue_snapshot_id: str
    queue_card_keys: tuple[str, ...]
    foundation_sha256s: tuple[str, ...]
    manifest_sha256: str
    registered_at: dt.datetime


__all__ = (
    "IntradayResearchInputBindingError",
    "IntradayResearchInputBindingReceipt",
    "IntradayResearchInputBindingRequest",
    "IntradayResearchInputBindingResult",
    "IntradayResearchStrategyBinding",
)
