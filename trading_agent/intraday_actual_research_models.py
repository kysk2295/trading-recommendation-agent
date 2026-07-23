from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogResult,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingResult,
    IntradayResearchStrategyBinding,
)
from trading_agent.intraday_research_loop import IntradayResearchLoopResult


@dataclass(frozen=True, slots=True)
class IntradayActualResearchPaths:
    dataset_root: Path
    binding_root: Path
    entitlement_contract: Path
    source_queue_artifact: Path
    lane_registry: Path
    experiment_ledger: Path
    artifact_root: Path
    review_root: Path


@dataclass(frozen=True, slots=True)
class IntradayActualResearchRequest:
    session_dirs: tuple[Path, ...]
    required_session_dates: tuple[dt.date, ...]
    strategy_bindings: tuple[IntradayResearchStrategyBinding, ...]
    dataset_producer_commit_sha: str
    code_version: str
    registered_at: dt.datetime
    observed_at: dt.datetime
    minimum_clean_sessions: int
    minimum_training_sessions: int
    max_sessions: int
    max_bars: int
    per_side_fee_bps: int
    per_side_slippage_bps: int
    bootstrap_samples: int
    rss_limit_gib: float
    paths: IntradayActualResearchPaths


@dataclass(frozen=True, slots=True)
class IntradayActualResearchResult:
    catalog: IntradayResearchDatasetCatalogResult
    binding: IntradayResearchInputBindingResult
    loop: IntradayResearchLoopResult


__all__ = (
    "IntradayActualResearchPaths",
    "IntradayActualResearchRequest",
    "IntradayActualResearchResult",
)
