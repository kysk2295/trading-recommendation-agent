from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.kr_theme_lane import (
    KR_THEME_LEADER_VWAP_RECLAIM_LANE,
    KR_THEME_OPPORTUNITY_LANE,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    kr_theme_strategy_version,
)
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import AgentOperatingMode

_HYPOTHESIS_PREFIX: Final = "H-KR-THEME-DAY-COMPOSITE-"
_RULE_PREFIX: Final = "rank_one_opportunity_then_vwap_reclaim"


class InvalidKrThemeDayCompositeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day composite experiment is invalid"


class KrThemeDayCompositeRegistrationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day_strategy_version: str
    opportunity_strategy_version: str
    registered_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        versions = (self.day_strategy_version, self.opportunity_strategy_version)
        if not _aware(self.registered_at) or any(not value or value != value.strip() for value in versions):
            raise InvalidKrThemeDayCompositeError
        return self


class KrThemeDayCompositeAuthorityRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day_strategy_version: str
    opportunity_strategy_version: str
    as_of: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        versions = (self.day_strategy_version, self.opportunity_strategy_version)
        if not _aware(self.as_of) or any(not value or value != value.strip() for value in versions):
            raise InvalidKrThemeDayCompositeError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayCompositeAuthority:
    hypothesis_id: str
    registration_key: str
    day_strategy_version: str
    opportunity_strategy_version: str
    registered_at: dt.datetime


@dataclass(frozen=True, slots=True)
class KrThemeDayCompositeRegistrationResult:
    created: bool
    authority: KrThemeDayCompositeAuthority


def kr_theme_day_composite_hypothesis_id(day_strategy_version: str, opportunity_strategy_version: str) -> str:
    if not day_strategy_version or not opportunity_strategy_version:
        raise InvalidKrThemeDayCompositeError
    material = f"{day_strategy_version}|{opportunity_strategy_version}|{_RULE_PREFIX}"
    return f"{_HYPOTHESIS_PREFIX}{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def register_kr_theme_day_composite(
    ledger: ExperimentLedgerStore,
    request: KrThemeDayCompositeRegistrationRequest,
) -> KrThemeDayCompositeRegistrationResult:
    try:
        request = KrThemeDayCompositeRegistrationRequest.model_validate(request.model_dump(mode="python"))
        day, opportunity = _component_versions(
            ledger,
            request.day_strategy_version,
            request.opportunity_strategy_version,
        )
        registration = _registration(request, day, opportunity)
        with ledger.writer() as writer:
            created = writer.register_multi_market_hypothesis(registration)
        authority = _authority(registration)
    except (
        AttributeError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayCompositeError from None
    return KrThemeDayCompositeRegistrationResult(created, authority)


def require_exact_kr_theme_day_composite(
    ledger: ExperimentLedgerReader,
    request: KrThemeDayCompositeAuthorityRequest,
) -> KrThemeDayCompositeAuthority:
    try:
        request = KrThemeDayCompositeAuthorityRequest.model_validate(request.model_dump(mode="python"))
        day, opportunity = _component_versions(
            ledger,
            request.day_strategy_version,
            request.opportunity_strategy_version,
        )
        hypothesis_id = kr_theme_day_composite_hypothesis_id(day.strategy_version, opportunity.strategy_version)
        matches = tuple(
            item for item in ledger.multi_market_hypotheses() if item.registration.hypothesis_id == hypothesis_id
        )
        if len(matches) != 1:
            raise InvalidKrThemeDayCompositeError
        stored = matches[0]
        expected = _registration(
            KrThemeDayCompositeRegistrationRequest(
                day_strategy_version=day.strategy_version,
                opportunity_strategy_version=opportunity.strategy_version,
                registered_at=stored.registration.source_registered_at,
            ),
            day,
            opportunity,
        )
        if stored.registration != expected or request.as_of < expected.ledger_recorded_at:
            raise InvalidKrThemeDayCompositeError
        return _authority(expected)
    except (AttributeError, InvalidExperimentLedgerSourceError, ValidationError, ValueError):
        raise InvalidKrThemeDayCompositeError from None


def _component_versions(
    ledger: ExperimentLedgerReader,
    day_strategy_version: str,
    opportunity_strategy_version: str,
) -> tuple[MultiMarketStrategyVersionRegistration, MultiMarketStrategyVersionRegistration]:
    versions = tuple(item.registration for item in ledger.multi_market_strategy_versions())
    day_matches = tuple(item for item in versions if item.strategy_version == day_strategy_version)
    opportunity_matches = tuple(item for item in versions if item.strategy_version == opportunity_strategy_version)
    if len(day_matches) != 1 or len(opportunity_matches) != 1:
        raise InvalidKrThemeDayCompositeError
    day, opportunity = day_matches[0], opportunity_matches[0]
    if (
        day.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or opportunity.strategy_lane != KR_THEME_OPPORTUNITY_LANE
        or day.operating_mode is not AgentOperatingMode.SHADOW
        or opportunity.operating_mode is not AgentOperatingMode.SHADOW
        or day.strategy_version != kr_theme_day_strategy_version(day.code_version)
        or opportunity.strategy_version != kr_theme_strategy_version(opportunity.code_version)
    ):
        raise InvalidKrThemeDayCompositeError
    return day, opportunity


def _registration(
    request: KrThemeDayCompositeRegistrationRequest,
    day: MultiMarketStrategyVersionRegistration,
    opportunity: MultiMarketStrategyVersionRegistration,
) -> MultiMarketHypothesisRegistration:
    if request.registered_at < max(day.ledger_recorded_at, opportunity.ledger_recorded_at):
        raise InvalidKrThemeDayCompositeError
    hypothesis_id = kr_theme_day_composite_hypothesis_id(day.strategy_version, opportunity.strategy_version)
    rule = f"{_RULE_PREFIX}:{opportunity.strategy_version}->{day.strategy_version}"
    lanes = tuple(sorted((day.strategy_lane, opportunity.strategy_lane), key=lambda item: item.canonical_id))
    scope = MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.CROSS_LANE_HYPOTHESIS,
        hypothesis_id=hypothesis_id,
        primary_lane=day.strategy_lane,
        lanes=lanes,
        source_hypothesis_ids=tuple(sorted((day.hypothesis_id, opportunity.hypothesis_id))),
        combination_rule=rule,
        registered_at=request.registered_at,
    )
    return MultiMarketHypothesisRegistration(
        hypothesis_id=hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=multi_market_experiment_scope_key(scope),
        hypothesis=(
            "A same-cycle rank-one KR theme Opportunity may define the sole candidate "
            "for the fixed day VWAP reclaim rule."
        ),
        falsification_rule=(
            "Reject the composite when preregistered forward attribution, fillability, "
            "stability, or coverage gates fail."
        ),
        source_registered_at=request.registered_at,
        ledger_recorded_at=request.registered_at,
    )


def _authority(registration: MultiMarketHypothesisRegistration) -> KrThemeDayCompositeAuthority:
    rule = registration.experiment_scope.combination_rule
    if rule is None:
        raise InvalidKrThemeDayCompositeError
    versions = rule.removeprefix(f"{_RULE_PREFIX}:").split("->")
    if len(versions) != 2:
        raise InvalidKrThemeDayCompositeError
    return KrThemeDayCompositeAuthority(
        hypothesis_id=registration.hypothesis_id,
        registration_key=str(multi_market_hypothesis_registration_key(registration)),
        day_strategy_version=versions[1],
        opportunity_strategy_version=versions[0],
        registered_at=registration.source_registered_at,
    )


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
