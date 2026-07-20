from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    ExperimentLedgerStore,
)
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    MarketId,
    StrategyLaneRef,
)

US_NEWS_CATALYST_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.OPPORTUNITY_MANAGER,
    strategy_id="news_catalyst",
)
US_NEWS_CATALYST_HYPOTHESIS_ID: Final = "H-US-NEWS-CATALYST-001"
US_NEWS_CATALYST_PARAMETER_SET: Final = (
    "event_freshness_seconds=300",
    "ranking=recent_article_count_desc,latest_provider_updated_at_desc,symbol_asc",
    "top_n=20",
    "validity_seconds=300",
)
US_NEWS_CATALYST_DATA_CONTRACT: Final = (
    "alpaca_news_complete_coverage_v1",
    "alpaca_news_opportunity_evidence_v1",
    "bounded_declared_us_equity_universe",
    "provider_updated_at_lte_coverage_cutoff",
)
US_NEWS_CATALYST_COST_MODEL: Final = (
    "opportunity_discovery_only",
    "trading_cost_not_evaluated",
)
US_NEWS_CATALYST_PORTFOLIO_POLICY: Final = (
    "downstream_validation_required",
    "no_entry_or_direction",
    "no_order_authority",
    "no_position_sizing",
)
_STRATEGY_VERSION_BASE: Final = "us-news-catalyst-recency-v1"


class InvalidUsNewsCatalystResearchRegistrationError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst research registration is invalid"


class UsNewsCatalystResearchRegistrationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    hypothesis_id: str
    strategy_version: str
    code_version: str
    hypothesis: str
    falsification_rule: str
    parameter_set: tuple[str, ...]
    data_contract: tuple[str, ...]
    cost_model: tuple[str, ...]
    portfolio_policy: tuple[str, ...]
    source_registered_at: dt.datetime
    ledger_recorded_at: dt.datetime

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if (
            self.hypothesis_id != US_NEWS_CATALYST_HYPOTHESIS_ID
            or self.strategy_version != us_news_catalyst_strategy_version(self.code_version)
            or not _canonical_text(self.hypothesis)
            or not _canonical_text(self.falsification_rule)
            or self.parameter_set != US_NEWS_CATALYST_PARAMETER_SET
            or self.data_contract != US_NEWS_CATALYST_DATA_CONTRACT
            or self.cost_model != US_NEWS_CATALYST_COST_MODEL
            or self.portfolio_policy != US_NEWS_CATALYST_PORTFOLIO_POLICY
            or not _aware(self.source_registered_at)
            or not _aware(self.ledger_recorded_at)
            or self.ledger_recorded_at < self.source_registered_at
        ):
            raise InvalidUsNewsCatalystResearchRegistrationError
        return self


class UsNewsCatalystProjectionAuthorityRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    projected_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            self.strategy_version != us_news_catalyst_strategy_version(self.code_version)
            or not _aware(self.projected_at)
        ):
            raise InvalidUsNewsCatalystResearchRegistrationError
        return self


@dataclass(frozen=True, slots=True)
class UsNewsCatalystResearchRegistrationResult:
    hypotheses_created: int
    versions_created: int
    strategy_version: str
    strategy_lane: StrategyLaneRef


def us_news_catalyst_strategy_version(code_version: str) -> str:
    if not _canonical_text(code_version):
        raise InvalidUsNewsCatalystResearchRegistrationError
    digest = hashlib.sha256(code_version.encode()).hexdigest()[:16]
    return f"{_STRATEGY_VERSION_BASE}-code-{digest}"


def load_us_news_catalyst_research_manifest(
    path: Path,
) -> UsNewsCatalystResearchRegistrationManifest:
    try:
        return UsNewsCatalystResearchRegistrationManifest.model_validate_json(path.read_bytes())
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystResearchRegistrationError from None


def register_us_news_catalyst_research_manifest(
    manifest_path: Path,
    ledger: ExperimentLedgerStore,
) -> UsNewsCatalystResearchRegistrationResult:
    manifest = load_us_news_catalyst_research_manifest(manifest_path)
    hypothesis, version = _registrations(manifest)
    with ledger.writer() as writer:
        hypotheses_created = int(writer.register_multi_market_hypothesis(hypothesis))
        versions_created = int(writer.register_multi_market_strategy_version(version))
    return UsNewsCatalystResearchRegistrationResult(
        hypotheses_created=hypotheses_created,
        versions_created=versions_created,
        strategy_version=version.strategy_version,
        strategy_lane=version.strategy_lane,
    )


def require_registered_us_news_catalyst_strategy(
    ledger: ExperimentLedgerReader,
    request: UsNewsCatalystProjectionAuthorityRequest,
) -> MultiMarketStrategyVersionRegistration:
    matches = tuple(
        stored.registration
        for stored in ledger.multi_market_strategy_versions()
        if stored.registration.strategy_version == request.strategy_version
    )
    if len(matches) != 1:
        raise InvalidUsNewsCatalystResearchRegistrationError
    registration = matches[0]
    if (
        registration.hypothesis_id != US_NEWS_CATALYST_HYPOTHESIS_ID
        or registration.strategy_lane != US_NEWS_CATALYST_LANE
        or registration.operating_mode is not AgentOperatingMode.SHADOW
        or registration.code_version != request.code_version
        or registration.parameter_set != US_NEWS_CATALYST_PARAMETER_SET
        or registration.data_contract != US_NEWS_CATALYST_DATA_CONTRACT
        or registration.cost_model != US_NEWS_CATALYST_COST_MODEL
        or registration.portfolio_policy != US_NEWS_CATALYST_PORTFOLIO_POLICY
        or request.projected_at < registration.source_registered_at
    ):
        raise InvalidUsNewsCatalystResearchRegistrationError
    return registration


def _registrations(
    manifest: UsNewsCatalystResearchRegistrationManifest,
) -> tuple[MultiMarketHypothesisRegistration, MultiMarketStrategyVersionRegistration]:
    scope = MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id=manifest.hypothesis_id,
        primary_lane=US_NEWS_CATALYST_LANE,
        lanes=(US_NEWS_CATALYST_LANE,),
        registered_at=manifest.source_registered_at,
    )
    scope_key = multi_market_experiment_scope_key(scope)
    hypothesis = MultiMarketHypothesisRegistration(
        hypothesis_id=manifest.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=scope_key,
        hypothesis=manifest.hypothesis,
        falsification_rule=manifest.falsification_rule,
        source_registered_at=manifest.source_registered_at,
        ledger_recorded_at=manifest.ledger_recorded_at,
    )
    version = MultiMarketStrategyVersionRegistration(
        strategy_version=manifest.strategy_version,
        hypothesis_id=manifest.hypothesis_id,
        experiment_scope_key=scope_key,
        strategy_lane=US_NEWS_CATALYST_LANE,
        operating_mode=AgentOperatingMode.SHADOW,
        code_version=manifest.code_version,
        parameter_set=manifest.parameter_set,
        data_contract=manifest.data_contract,
        cost_model=manifest.cost_model,
        portfolio_policy=manifest.portfolio_policy,
        source_registered_at=manifest.source_registered_at,
        ledger_recorded_at=manifest.ledger_recorded_at,
    )
    return hypothesis, version


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "US_NEWS_CATALYST_LANE",
    "InvalidUsNewsCatalystResearchRegistrationError",
    "UsNewsCatalystProjectionAuthorityRequest",
    "UsNewsCatalystResearchRegistrationResult",
    "load_us_news_catalyst_research_manifest",
    "register_us_news_catalyst_research_manifest",
    "require_registered_us_news_catalyst_strategy",
    "us_news_catalyst_strategy_version",
)
