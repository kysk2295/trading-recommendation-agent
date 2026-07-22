from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, override

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import AgentOperatingMode
from trading_agent.systematic_regime_engine import SYSTEMATIC_REGIME_LANE

SYSTEMATIC_REGIME_HYPOTHESIS_ID: Final = "H-US-SYSTEMATIC-REGIME-001"
_VERSION_BASE: Final = "us-regime-rotation-v1"


class InvalidSystematicRegimeResearchError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime research registration is invalid"


@dataclass(frozen=True, slots=True)
class SystematicRegimeResearch:
    hypothesis: MultiMarketHypothesisRegistration
    version: MultiMarketStrategyVersionRegistration


def systematic_regime_strategy_version(code_version: str) -> str:
    if not code_version or code_version != code_version.strip():
        raise InvalidSystematicRegimeResearchError
    digest = hashlib.sha256(code_version.encode()).hexdigest()[:16]
    return f"{_VERSION_BASE}-code-{digest}"


def ensure_systematic_regime_research(
    ledger: ExperimentLedgerStore,
    code_version: str,
    recorded_at: dt.datetime,
) -> SystematicRegimeResearch:
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise InvalidSystematicRegimeResearchError
    hypotheses = tuple(
        item.registration
        for item in ledger.multi_market_hypotheses()
        if item.registration.hypothesis_id == SYSTEMATIC_REGIME_HYPOTHESIS_ID
    )
    if len(hypotheses) > 1:
        raise InvalidSystematicRegimeResearchError
    hypothesis = hypotheses[0] if hypotheses else _hypothesis(recorded_at)
    version_id = systematic_regime_strategy_version(code_version)
    versions = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.strategy_version == version_id
    )
    if len(versions) > 1:
        raise InvalidSystematicRegimeResearchError
    version = versions[0] if versions else _version(hypothesis, code_version, recorded_at)
    _require_exact(hypothesis, version, code_version)
    with ledger.writer() as writer:
        _ = writer.register_multi_market_hypothesis(hypothesis)
        _ = writer.register_multi_market_strategy_version(version)
    return SystematicRegimeResearch(hypothesis, version)


def _hypothesis(recorded_at: dt.datetime) -> MultiMarketHypothesisRegistration:
    scope = MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id=SYSTEMATIC_REGIME_HYPOTHESIS_ID,
        primary_lane=SYSTEMATIC_REGIME_LANE,
        lanes=(SYSTEMATIC_REGIME_LANE,),
        registered_at=recorded_at,
    )
    return MultiMarketHypothesisRegistration(
        hypothesis_id=SYSTEMATIC_REGIME_HYPOTHESIS_ID,
        experiment_scope=scope,
        experiment_scope_key=multi_market_experiment_scope_key(scope),
        hypothesis=(
            "Completed equity trend and breadth identify which risk-on or defensive "
            "ETF sleeve leads next session."
        ),
        falsification_rule=(
            "Reject if causal net forward returns fail after fixed costs or regime "
            "cohorts are unstable."
        ),
        source_registered_at=recorded_at,
        ledger_recorded_at=recorded_at,
    )


def _version(
    hypothesis: MultiMarketHypothesisRegistration,
    code_version: str,
    recorded_at: dt.datetime,
) -> MultiMarketStrategyVersionRegistration:
    return MultiMarketStrategyVersionRegistration(
        strategy_version=systematic_regime_strategy_version(code_version),
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_scope_key=hypothesis.experiment_scope_key,
        strategy_lane=SYSTEMATIC_REGIME_LANE,
        operating_mode=AgentOperatingMode.SHADOW,
        code_version=code_version,
        parameter_set=("breadth_50_sessions_2_of_3", "momentum_20_sessions", "trend_200_sessions"),
        data_contract=("completed_daily_ohlcv", "fixed_six_etf_universe", "next_session_causal_evaluation"),
        cost_model=("round_trip_40_bps",),
        portfolio_policy=("equal_weight_two_candidates", "no_account_or_order_authority", "signal_only_market_context"),
        source_registered_at=hypothesis.source_registered_at,
        ledger_recorded_at=recorded_at,
    )


def _require_exact(
    hypothesis: MultiMarketHypothesisRegistration,
    version: MultiMarketStrategyVersionRegistration,
    code_version: str,
) -> None:
    if (
        hypothesis.experiment_scope.primary_lane != SYSTEMATIC_REGIME_LANE
        or version.hypothesis_id != hypothesis.hypothesis_id
        or version.experiment_scope_key != hypothesis.experiment_scope_key
        or version.strategy_lane != SYSTEMATIC_REGIME_LANE
        or version.operating_mode is not AgentOperatingMode.SHADOW
        or version.code_version != code_version
        or version.strategy_version != systematic_regime_strategy_version(code_version)
    ):
        raise InvalidSystematicRegimeResearchError


__all__ = (
    "SYSTEMATIC_REGIME_HYPOTHESIS_ID",
    "InvalidSystematicRegimeResearchError",
    "SystematicRegimeResearch",
    "ensure_systematic_regime_research",
    "systematic_regime_strategy_version",
)
