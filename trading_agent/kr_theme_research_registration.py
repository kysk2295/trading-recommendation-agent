from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    ExperimentLedgerStore,
)
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import AgentOperatingMode

_HYPOTHESIS_ID = "H-KR-THEME-MOMENTUM-001"
_STRATEGY_VERSION_BASE = "kr-theme-keyword-projection-v1"


class InvalidKrThemeResearchRegistrationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme research registration is invalid"


class KrThemeResearchRegistrationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
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
        contracts = (
            self.parameter_set,
            self.data_contract,
            self.cost_model,
            self.portfolio_policy,
        )
        if (
            self.schema_version != 1
            or self.hypothesis_id != _HYPOTHESIS_ID
            or self.strategy_version != kr_theme_strategy_version(self.code_version)
            or not _canonical_text(self.hypothesis)
            or not _canonical_text(self.falsification_rule)
            or not all(_ordered_contract(values) for values in contracts)
            or not _aware(self.source_registered_at)
            or not _aware(self.ledger_recorded_at)
            or self.ledger_recorded_at < self.source_registered_at
        ):
            raise InvalidKrThemeResearchRegistrationError
        return self


class KrThemeProjectionAuthorityRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    projected_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.strategy_version != kr_theme_strategy_version(self.code_version) or not _aware(self.projected_at):
            raise InvalidKrThemeResearchRegistrationError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeResearchRegistrationResult:
    hypotheses_created: int
    versions_created: int
    strategy_version: str


def kr_theme_strategy_version(code_version: str) -> str:
    if not code_version or code_version != code_version.strip():
        raise InvalidKrThemeResearchRegistrationError
    digest = hashlib.sha256(code_version.encode()).hexdigest()[:16]
    return f"{_STRATEGY_VERSION_BASE}-code-{digest}"


def register_kr_theme_research_manifest(
    manifest_path: Path,
    ledger: ExperimentLedgerStore,
) -> KrThemeResearchRegistrationResult:
    manifest = load_kr_theme_research_manifest(manifest_path)
    hypothesis, version = _registrations(manifest)
    with ledger.writer() as writer:
        hypotheses_created = int(writer.register_multi_market_hypothesis(hypothesis))
        versions_created = int(writer.register_multi_market_strategy_version(version))
    return KrThemeResearchRegistrationResult(
        hypotheses_created=hypotheses_created,
        versions_created=versions_created,
        strategy_version=version.strategy_version,
    )


def require_registered_kr_theme_strategy(
    ledger: ExperimentLedgerReader,
    request: KrThemeProjectionAuthorityRequest,
) -> MultiMarketStrategyVersionRegistration:
    matches = tuple(
        stored.registration
        for stored in ledger.multi_market_strategy_versions()
        if stored.registration.strategy_version == request.strategy_version
    )
    if len(matches) != 1:
        raise InvalidKrThemeResearchRegistrationError
    registration = matches[0]
    if (
        registration.code_version != request.code_version
        or registration.strategy_lane != KR_THEME_OPPORTUNITY_LANE
        or registration.operating_mode is not AgentOperatingMode.SHADOW
        or request.projected_at < registration.source_registered_at
    ):
        raise InvalidKrThemeResearchRegistrationError
    return registration


def load_kr_theme_research_manifest(
    path: Path,
) -> KrThemeResearchRegistrationManifest:
    try:
        return KrThemeResearchRegistrationManifest.model_validate_json(path.read_bytes())
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeResearchRegistrationError from None


def _registrations(
    manifest: KrThemeResearchRegistrationManifest,
) -> tuple[MultiMarketHypothesisRegistration, MultiMarketStrategyVersionRegistration]:
    scope = MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id=manifest.hypothesis_id,
        primary_lane=KR_THEME_OPPORTUNITY_LANE,
        lanes=(KR_THEME_OPPORTUNITY_LANE,),
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
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
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


def _ordered_contract(values: tuple[str, ...]) -> bool:
    return bool(values) and len(values) == len(set(values)) and all(_canonical_text(value) for value in values)


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
