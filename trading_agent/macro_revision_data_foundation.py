from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Sequence
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.alfred_revision_release_gate import (
    AlfredRevisionReleaseAssessment,
    build_alfred_revision_release_assessment,
)
from trading_agent.data_capability_models import (
    DataCapability,
    DataDeliveryMode,
    DataEntitlement,
    DataRequirementFailureMode,
    DataSourceId,
    DataUse,
    StrategyDataRequirement,
    TimestampSemantic,
)
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_vintage_dates_models import FredVintageDatesSnapshot
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.strategy_data_gate import StrategyDataStatus

_SHA = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_IDS: Final = (
    "alfred/vintage_observations",
    "fred/series_observations",
    "fred/series_vintage_dates",
)
_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.MARKET_CONTEXT,
    strategy_id="macro_revision_context",
)


class MacroRevisionDataFoundationError(ValueError):
    @override
    def __str__(self) -> str:
        return "macro revision data foundation is invalid"


class MacroRevisionDataFoundation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    data_manifest: DataFoundationManifest
    panel_id: str
    panel_file_sha256: str
    calendar_snapshot_id: str
    calendar_file_sha256: str
    release_assessment: AlfredRevisionReleaseAssessment
    release_assessment_file_sha256: str

    @model_validator(mode="after")
    def validate_foundation(self) -> Self:
        source_ids = tuple(
            capability.source_id.canonical_id
            for capability in self.data_manifest.capabilities
        )
        requirement_ids = tuple(
            requirement.requirement_id
            for requirement in self.data_manifest.requirements
        )
        if (
            any(
                _SHA.fullmatch(value) is None
                for value in (
                    self.panel_id,
                    self.panel_file_sha256,
                    self.calendar_snapshot_id,
                    self.calendar_file_sha256,
                    self.release_assessment_file_sha256,
                )
            )
            or self.data_manifest.strategy_lane != _LANE
            or source_ids != _SOURCE_IDS
            or requirement_ids
            != (
                "macro-latest-observation",
                "macro-release-date",
                "macro-vintage-observation",
            )
            or self.release_assessment.panel_id != self.panel_id
            or self.release_assessment.calendar_snapshot_id
            != self.calendar_snapshot_id
            or self.data_manifest.evaluate_data_readiness().status
            is not StrategyDataStatus.READY
        ):
            raise MacroRevisionDataFoundationError
        return self

    @property
    def foundation_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def build_macro_revision_data_foundation(
    *,
    panel: AlfredRevisionPanel,
    calendar: FredVintageDatesSnapshot,
    assessment: AlfredRevisionReleaseAssessment,
    panel_file_sha256: str,
    calendar_file_sha256: str,
    assessment_file_sha256: str,
    capabilities: Sequence[DataCapability],
    entitlements: Sequence[DataEntitlement],
    evaluated_at: dt.datetime,
) -> MacroRevisionDataFoundation:
    try:
        checked_panel = AlfredRevisionPanel.model_validate(
            panel.model_dump(mode="python")
        )
        checked_calendar = FredVintageDatesSnapshot.model_validate(
            calendar.model_dump(mode="python")
        )
        checked_assessment = AlfredRevisionReleaseAssessment.model_validate(
            assessment.model_dump(mode="python")
        )
        expected_assessment = build_alfred_revision_release_assessment(
            checked_panel,
            checked_calendar,
        )
        ordered_capabilities = tuple(
            sorted(
                (
                    DataCapability.model_validate(item.model_dump(mode="python"))
                    for item in capabilities
                ),
                key=lambda item: item.source_id.canonical_id,
            )
        )
        ordered_entitlements = tuple(
            sorted(
                (
                    DataEntitlement.model_validate(item.model_dump(mode="python"))
                    for item in entitlements
                ),
                key=lambda item: item.source_id.canonical_id,
            )
        )
        source_ids = tuple(
            item.source_id.canonical_id for item in ordered_capabilities
        )
        entitlement_sources = tuple(
            item.source_id.canonical_id for item in ordered_entitlements
        )
        if (
            checked_assessment != expected_assessment
            or source_ids != _SOURCE_IDS
            or entitlement_sources != _SOURCE_IDS
            or not _aware(evaluated_at)
            or evaluated_at < checked_assessment.assessed_at
            or any(
                _SHA.fullmatch(value) is None
                for value in (
                    panel_file_sha256,
                    calendar_file_sha256,
                    assessment_file_sha256,
                )
            )
        ):
            raise MacroRevisionDataFoundationError
        requirements = _requirements(checked_panel, checked_calendar)
        manifest = DataFoundationManifest(
            manifest_id=(
                "macro-revision-"
                f"{checked_assessment.assessment_id[:16]}"
            ),
            registered_at=evaluated_at,
            evaluated_at=evaluated_at,
            strategy_lane=_LANE,
            capabilities=ordered_capabilities,
            entitlements=ordered_entitlements,
            requirements=requirements,
        )
        return MacroRevisionDataFoundation(
            data_manifest=manifest,
            panel_id=checked_panel.panel_id,
            panel_file_sha256=panel_file_sha256,
            calendar_snapshot_id=checked_calendar.snapshot_id,
            calendar_file_sha256=calendar_file_sha256,
            release_assessment=checked_assessment,
            release_assessment_file_sha256=assessment_file_sha256,
        )
    except MacroRevisionDataFoundationError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise MacroRevisionDataFoundationError from None


def _requirements(
    panel: AlfredRevisionPanel,
    calendar: FredVintageDatesSnapshot,
) -> tuple[StrategyDataRequirement, ...]:
    return (
        _requirement(
            "macro-latest-observation",
            DataSourceId(provider="fred", feed="series_observations"),
            "macro_observation",
            panel.observation_start,
        ),
        _requirement(
            "macro-release-date",
            DataSourceId(provider="fred", feed="series_vintage_dates"),
            "macro_release_date",
            min(calendar.vintage_dates),
        ),
        _requirement(
            "macro-vintage-observation",
            DataSourceId(provider="alfred", feed="vintage_observations"),
            "macro_observation",
            panel.observation_start,
        ),
    )


def _requirement(
    requirement_id: str,
    source_id: DataSourceId,
    event_type: str,
    historical_start: dt.date,
) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        requirement_id=requirement_id,
        strategy_lane=_LANE,
        data_use=DataUse.HISTORICAL_RESEARCH,
        market_domain=DataMarketDomain.GLOBAL_MACRO,
        event_type=event_type,
        primary_source_id=source_id,
        required_delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
        required_timestamp_semantics=(TimestampSemantic.EVENT_TIME,),
        max_age_seconds=86_400,
        minimum_completeness_bps=10_000,
        minimum_historical_start=historical_start,
        allow_degraded=False,
        failure_mode=DataRequirementFailureMode.BLOCKED_BY_DATA,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "MacroRevisionDataFoundation",
    "MacroRevisionDataFoundationError",
    "build_macro_revision_data_foundation",
)
