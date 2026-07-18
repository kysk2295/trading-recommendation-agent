from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from trading_agent.intraday_feature_kernel import IntradayFeatureSnapshot
from trading_agent.research_identity_models import StrategyLaneRef
from trading_agent.signal_contract_models import OpportunitySnapshot


class UsFeatureGateBlockedReason(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    SYMBOL_COVERAGE = "symbol_coverage"
    FEATURE_GAP = "feature_gap"
    FEATURE_STALE = "feature_stale"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NONCAUSAL_EVIDENCE = "noncausal_evidence"
    OPPORTUNITY_EXPIRED = "opportunity_expired"


@dataclass(frozen=True, slots=True)
class UsFeatureEvidenceBinding:
    symbol: str
    snapshot: IntradayFeatureSnapshot


@dataclass(frozen=True, slots=True)
class UsFeatureGateReady:
    opportunity: OpportunitySnapshot


@dataclass(frozen=True, slots=True)
class UsFeatureGateBlocked:
    reason: UsFeatureGateBlockedReason
    base_opportunity_id: str
    evaluated_at: dt.datetime


type UsFeatureGateResult = UsFeatureGateReady | UsFeatureGateBlocked


@dataclass(frozen=True, slots=True)
class EvidenceGatedSignalRequest:
    strategy_lane: StrategyLaneRef
    strategy_version: str
    published_at: dt.datetime
    created_after: dt.datetime
