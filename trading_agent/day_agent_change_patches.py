from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentChangeKind(StrEnum):
    MARKET_REGIME_POLICY = "market_regime_policy"
    THEME_SELECTION_POLICY = "theme_selection_policy"
    CATALYST_INTERPRETATION_POLICY = "catalyst_interpretation_policy"
    LEADER_RANKING_POLICY = "leader_ranking_policy"
    FLOW_INTERPRETATION_POLICY = "flow_interpretation_policy"
    ENTRY_POLICY = "entry_policy"
    EXIT_POLICY = "exit_policy"
    EXECUTION_REVIEW_POLICY = "execution_review_policy"


class MarketRegimeRule(StrEnum):
    TREND_ALIGNMENT = "trend_alignment"
    VOLATILITY_CONTRACTION = "volatility_contraction"


class ThemeTimingWindow(StrEnum):
    OPENING_30_MINUTES = "opening_30_minutes"
    OPENING_60_MINUTES = "opening_60_minutes"


class CatalystRule(StrEnum):
    FRESHNESS_FIRST = "freshness_first"
    CONFIRMATION_FIRST = "confirmation_first"


class LeaderRankingFeature(StrEnum):
    RELATIVE_VOLUME = "relative_volume"
    DOLLAR_VOLUME = "dollar_volume"
    GAP_STRENGTH = "gap_strength"


class FlowInterpretationRule(StrEnum):
    SPREAD_CONFIRMATION = "spread_confirmation"
    VOLUME_CONFIRMATION = "volume_confirmation"


class EntryRule(StrEnum):
    BREAKOUT_CONFIRMATION = "breakout_confirmation"
    PULLBACK_CONFIRMATION = "pullback_confirmation"


class ExitRule(StrEnum):
    R_MULTIPLE_TARGETS = "r_multiple_targets"
    TRAILING_STRUCTURE = "trailing_structure"


class ExecutionReviewRule(StrEnum):
    SLIPPAGE_ATTRIBUTION = "slippage_attribution"
    FILL_QUALITY_ATTRIBUTION = "fill_quality_attribution"


class AgentPatchModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class MarketRegimePatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.MARKET_REGIME_POLICY]
    rule: MarketRegimeRule
    confirmation_bars: int = Field(ge=1, le=5)


class ThemeSelectionPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.THEME_SELECTION_POLICY]
    timing_window: ThemeTimingWindow
    minimum_catalyst_count: int = Field(ge=1, le=5)


class CatalystInterpretationPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.CATALYST_INTERPRETATION_POLICY]
    rule: CatalystRule
    maximum_age_minutes: int = Field(ge=1, le=390)


class LeaderRankingPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.LEADER_RANKING_POLICY]
    feature: LeaderRankingFeature
    weight_bps: int = Field(ge=0, le=10_000)


class FlowInterpretationPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.FLOW_INTERPRETATION_POLICY]
    rule: FlowInterpretationRule
    confirmation_bars: int = Field(ge=1, le=5)


class EntryPolicyPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.ENTRY_POLICY]
    rule: EntryRule
    confirmation_bars: int = Field(ge=1, le=5)


class ExitPolicyPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.EXIT_POLICY]
    rule: ExitRule
    trailing_window_bars: int = Field(ge=1, le=20)


class ExecutionReviewPatch(AgentPatchModel):
    kind: Literal[AgentChangeKind.EXECUTION_REVIEW_POLICY]
    rule: ExecutionReviewRule
    review_window_sessions: int = Field(ge=1, le=20)


type AgentVersionPatch = Annotated[
    MarketRegimePatch
    | ThemeSelectionPatch
    | CatalystInterpretationPatch
    | LeaderRankingPatch
    | FlowInterpretationPatch
    | EntryPolicyPatch
    | ExitPolicyPatch
    | ExecutionReviewPatch,
    Field(discriminator="kind"),
]


__all__ = (
    "AgentChangeKind",
    "AgentVersionPatch",
    "CatalystInterpretationPatch",
    "CatalystRule",
    "EntryPolicyPatch",
    "EntryRule",
    "ExecutionReviewPatch",
    "ExecutionReviewRule",
    "ExitPolicyPatch",
    "ExitRule",
    "FlowInterpretationPatch",
    "FlowInterpretationRule",
    "LeaderRankingFeature",
    "LeaderRankingPatch",
    "MarketRegimePatch",
    "MarketRegimeRule",
    "ThemeSelectionPatch",
    "ThemeTimingWindow",
)
