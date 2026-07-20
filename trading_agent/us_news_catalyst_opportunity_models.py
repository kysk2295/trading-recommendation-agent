from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.signal_contract_models import OpportunitySnapshot

MAX_CANDIDATES = 20
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class UsNewsCatalystProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst Opportunity projection is invalid"


class UsNewsCatalystProjectionStatus(StrEnum):
    NO_CANDIDATES = "no_candidates"
    RANKED = "ranked"


class UsNewsCatalystOpportunityProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    projection_id: str
    evidence_bundle_id: str
    strategy_registration_key: str
    strategy_version: str
    projected_at: dt.datetime
    status: UsNewsCatalystProjectionStatus
    eligible_symbol_count: int = Field(ge=0, le=MAX_CANDIDATES)
    snapshot: OpportunitySnapshot | None

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        ranked = self.status is UsNewsCatalystProjectionStatus.RANKED
        symbols = () if self.snapshot is None else tuple(item.symbol for item in self.snapshot.candidates)
        snapshot_matches = (
            self.snapshot is not None
            and self.snapshot.producer_strategy_version == self.strategy_version
            and self.snapshot.observed_at == self.projected_at
            and len(self.snapshot.candidates) == self.eligible_symbol_count
            and self.snapshot.opportunity_id
            == opportunity_identity(
                self.evidence_bundle_id,
                self.strategy_version,
                self.projected_at,
                symbols,
            )
        )
        if (
            _HEX64.fullmatch(self.projection_id) is None
            or self.projection_id
            != projection_identity(
                self.evidence_bundle_id,
                self.strategy_registration_key,
                self.strategy_version,
                self.projected_at,
                self.status,
                self.eligible_symbol_count,
                self.snapshot,
            )
            or _HEX64.fullmatch(self.evidence_bundle_id) is None
            or _HEX64.fullmatch(self.strategy_registration_key) is None
            or not _aware(self.projected_at)
            or (ranked and not snapshot_matches)
            or (not ranked and (self.snapshot is not None or self.eligible_symbol_count != 0))
        ):
            raise UsNewsCatalystProjectionError
        return self


def opportunity_identity(
    bundle_id: str,
    strategy_version: str,
    projected_at: dt.datetime,
    symbols: tuple[str, ...],
) -> str:
    stamp = projected_at.strftime("%Y%m%dT%H%M%S%fZ")
    digest = _identity(bundle_id, strategy_version, *symbols)
    return f"us-news-catalyst-{stamp}-{digest[:12]}"


def projection_identity(
    bundle_id: str,
    registration_key: str,
    strategy_version: str,
    projected_at: dt.datetime,
    status: UsNewsCatalystProjectionStatus,
    eligible_symbol_count: int,
    snapshot: OpportunitySnapshot | None,
) -> str:
    return _identity(
        bundle_id,
        registration_key,
        strategy_version,
        projected_at.isoformat(),
        status.value,
        str(eligible_symbol_count),
        "none" if snapshot is None else canonical_experiment_ledger_json(snapshot),
    )


def _identity(*parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "MAX_CANDIDATES",
    "UsNewsCatalystOpportunityProjection",
    "UsNewsCatalystProjectionError",
    "UsNewsCatalystProjectionStatus",
    "opportunity_identity",
    "projection_identity",
)
