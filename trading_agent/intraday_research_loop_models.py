from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.models import BarInput
from trading_agent.strategy_factory import StrategyMode

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROMOTION_MIN_SESSIONS: Final = 20
PROMOTION_MIN_TRADES: Final = 30
DEMOTION_MIN_SESSIONS: Final = 5
DEMOTION_MIN_TRADES: Final = 10


class InvalidIntradayResearchManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday research hypothesis bundle is invalid"


class IntradayHypothesisSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: StrategyMode
    hypothesis_id: str
    strategy_version: str | None = None
    queue_card_key: str | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        source_backed = self.strategy_version is not None or self.queue_card_key is not None
        if (
            self.strategy is StrategyMode.ORB
            or _IDENTIFIER.fullmatch(self.hypothesis_id) is None
            or (
                source_backed
                and (
                    self.strategy_version is None
                    or _IDENTIFIER.fullmatch(self.strategy_version) is None
                    or self.queue_card_key is None
                    or _HEX64.fullmatch(self.queue_card_key) is None
                )
            )
        ):
            raise InvalidIntradayResearchManifestError
        return self

    @property
    def is_source_backed(self) -> bool:
        return self.strategy_version is not None


class IntradayResearchManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1, 2] = 1
    family: Literal["intraday_challengers_v1", "source_backed_intraday_challengers_v2"]
    code_version: str
    hypotheses: tuple[IntradayHypothesisSelection, ...]
    source_queue_snapshot_id: str | None = None
    input_sha256: str | None = None
    registered_at: dt.datetime
    evaluator_version: Literal["intraday_walk_forward_v1"]
    minimum_training_sessions: int = Field(ge=0, le=20)
    max_bars: int = Field(ge=1, le=100_000)
    max_sessions: int = Field(ge=1, le=60)
    per_side_fee_bps: int = Field(ge=0, le=100)
    per_side_slippage_bps: int = Field(ge=0, le=100)
    bootstrap_samples: int = Field(ge=100, le=5_000)
    rss_limit_gib: float = Field(gt=0.0, le=9.5)

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        costs = self.per_side_fee_bps + self.per_side_slippage_bps
        strategies = self.strategies
        hypothesis_ids = tuple(item.hypothesis_id for item in self.hypotheses)
        source_backed = self.schema_version == 2
        if (
            _IDENTIFIER.fullmatch(self.code_version) is None
            or not self.hypotheses
            or len(self.hypotheses) > 3
            or len(set(strategies)) != len(strategies)
            or len(set(hypothesis_ids)) != len(hypothesis_ids)
            or costs < 20
            or costs > 100
            or self.registered_at.tzinfo is None
            or self.registered_at.utcoffset() is None
            or self.minimum_training_sessions >= self.max_sessions
            or source_backed is not (self.family == "source_backed_intraday_challengers_v2")
            or (
                source_backed
                and (
                    self.source_queue_snapshot_id is None
                    or _HEX64.fullmatch(self.source_queue_snapshot_id) is None
                    or self.input_sha256 is None
                    or _HEX64.fullmatch(self.input_sha256) is None
                    or any(not item.is_source_backed for item in self.hypotheses)
                )
            )
            or (
                not source_backed
                and (
                    self.source_queue_snapshot_id is not None
                    or self.input_sha256 is not None
                    or any(item.is_source_backed for item in self.hypotheses)
                )
            )
        ):
            raise InvalidIntradayResearchManifestError
        return self

    @property
    def strategies(self) -> tuple[StrategyMode, ...]:
        return tuple(item.strategy for item in self.hypotheses)

    @property
    def per_side_total_cost_bps(self) -> int:
        return self.per_side_fee_bps + self.per_side_slippage_bps


class IntradayReviewerDecision(StrEnum):
    PROMOTE = "promote"
    HOLD = "hold"
    DEMOTE = "demote"


class IntradayReviewEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_sessions: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    average_return: float | None
    profit_factor: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None


@dataclass(frozen=True, slots=True)
class IntradayWalkForwardRequest:
    bars: tuple[BarInput, ...]
    strategy: StrategyMode
    minimum_training_sessions: int
    per_side_cost_bps: int
    bootstrap_samples: int
    rss_limit_gib: float


@dataclass(frozen=True, slots=True)
class IntradayWalkForwardError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return f"intraday walk-forward blocked: {self.reason}"


class IntradayWalkForwardResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy: StrategyMode
    observed_sessions: int = Field(ge=1)
    fold_count: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    side_cost_bps: int = Field(ge=20, le=100)
    gross_average_return: float | None
    average_return: float | None
    profit_factor: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None
    peak_rss_gib: float = Field(ge=0.0, le=9.5)


def intraday_reviewer_decision(evidence: IntradayReviewEvidence) -> IntradayReviewerDecision:
    clear_failure = (
        evidence.observed_sessions >= DEMOTION_MIN_SESSIONS
        and evidence.trade_count >= DEMOTION_MIN_TRADES
        and evidence.profit_factor is not None
        and evidence.profit_factor < 0.75
        and evidence.average_return is not None
        and evidence.average_return < 0.0
        and evidence.mean_ci_high is not None
        and evidence.mean_ci_high < 0.0
    )
    if clear_failure:
        return IntradayReviewerDecision.DEMOTE
    promotion_ready = (
        evidence.observed_sessions >= PROMOTION_MIN_SESSIONS
        and evidence.trade_count >= PROMOTION_MIN_TRADES
        and evidence.profit_factor is not None
        and evidence.profit_factor >= 1.15
        and evidence.average_return is not None
        and evidence.average_return > 0.0
        and evidence.mean_ci_low is not None
        and evidence.mean_ci_low >= 0.0
    )
    if promotion_ready:
        return IntradayReviewerDecision.PROMOTE
    return IntradayReviewerDecision.HOLD


def load_intraday_research_manifest(path: Path) -> IntradayResearchManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IntradayResearchManifest.model_validate(payload)
    except (json.JSONDecodeError, OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidIntradayResearchManifestError from None


__all__ = (
    "IntradayHypothesisSelection",
    "IntradayResearchManifest",
    "IntradayReviewEvidence",
    "IntradayReviewerDecision",
    "IntradayWalkForwardError",
    "IntradayWalkForwardRequest",
    "IntradayWalkForwardResult",
    "InvalidIntradayResearchManifestError",
    "intraday_reviewer_decision",
    "load_intraday_research_manifest",
)
