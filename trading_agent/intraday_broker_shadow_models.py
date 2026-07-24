from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

if TYPE_CHECKING:
    from trading_agent.execution_ledger_reader import ReconciliationLedger
    from trading_agent.metrics import PaperTrade
    from trading_agent.paper_account_activity_store import StoredPaperAccountActivity
    from trading_agent.paper_protective_oco_recovery_store import StoredProtectiveOcoSnapshot

BROKER_SHADOW_EVIDENCE_VERSION: Final = "intraday_broker_shadow_promotion_v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class BrokerShadowEvidenceStatus(StrEnum):
    COLLECTING = "collecting"
    READY = "broker_shadow_ready"
    NOT_CONFIRMED = "broker_shadow_not_confirmed"


class BrokerShadowTradePair(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation_id: str
    session_date: dt.date
    symbol: str
    strategy_version: str
    broker_entry: float
    broker_exit: float
    shadow_entry: float
    shadow_exit: float
    broker_net_return: float
    shadow_net_return: float
    return_difference: float

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        values = (
            self.broker_entry,
            self.broker_exit,
            self.shadow_entry,
            self.shadow_exit,
            self.broker_net_return,
            self.shadow_net_return,
            self.return_difference,
        )
        if (
            not self.recommendation_id
            or not self.symbol
            or _IDENTIFIER.fullmatch(self.strategy_version) is None
            or not all(math.isfinite(value) for value in values)
            or min(values[:4]) <= 0
            or not math.isclose(
                self.return_difference,
                self.broker_net_return - self.shadow_net_return,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise InvalidBrokerShadowEvidenceError
        return self


class BrokerShadowMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_count: int
    average_return: float | None
    profit_factor: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        optional = (
            self.average_return,
            self.profit_factor,
            self.mean_ci_low,
            self.mean_ci_high,
        )
        if self.trade_count < 0 or any(
            value is not None and not math.isfinite(value)
            for value in optional
        ):
            raise InvalidBrokerShadowEvidenceError
        return self


@dataclass(frozen=True, slots=True)
class BrokerShadowAssessment:
    status: BrokerShadowEvidenceStatus
    blockers: tuple[str, ...]
    broker_metrics: BrokerShadowMetrics
    shadow_metrics: BrokerShadowMetrics


class BrokerShadowEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    evidence_version: Literal["intraday_broker_shadow_promotion_v1"]
    strategy_version: str
    execution_snapshot_sha256: str
    shadow_source_sha256: str
    reviewed_at: dt.datetime
    status: BrokerShadowEvidenceStatus
    pairs: tuple[BrokerShadowTradePair, ...]
    paired_trade_count: int
    paired_session_count: int
    unpaired_broker_intent_count: int
    broker_metrics: BrokerShadowMetrics
    shadow_metrics: BrokerShadowMetrics
    blockers: tuple[str, ...]
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        from trading_agent.intraday_broker_shadow_statistics import (
            assess_broker_shadow_pairs,
        )

        derived = assess_broker_shadow_pairs(
            self.pairs,
            self.unpaired_broker_intent_count,
        )
        if (
            _IDENTIFIER.fullmatch(self.strategy_version) is None
            or _HEX64.fullmatch(self.execution_snapshot_sha256) is None
            or _HEX64.fullmatch(self.shadow_source_sha256) is None
            or self.reviewed_at.tzinfo is None
            or self.reviewed_at.utcoffset() is None
            or self.pairs
            != tuple(
                sorted(
                    self.pairs,
                    key=lambda pair: (pair.session_date, pair.recommendation_id),
                )
            )
            or self.paired_trade_count != len(self.pairs)
            or not 0 <= self.paired_session_count <= self.paired_trade_count
            or self.paired_session_count
            != len({pair.session_date for pair in self.pairs})
            or self.unpaired_broker_intent_count < 0
            or self.blockers != tuple(sorted(set(self.blockers)))
            or any(
                pair.strategy_version != self.strategy_version
                for pair in self.pairs
            )
            or len({pair.recommendation_id for pair in self.pairs})
            != len(self.pairs)
            or self.status is not derived.status
            or self.blockers != derived.blockers
            or self.broker_metrics != derived.broker_metrics
            or self.shadow_metrics != derived.shadow_metrics
        ):
            raise InvalidBrokerShadowEvidenceError
        return self


class BrokerShadowEvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: BrokerShadowEvidence

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(
            canonical_experiment_ledger_json(self.payload).encode()
        ).hexdigest()
        if self.artifact_id != expected:
            raise InvalidBrokerShadowEvidenceError
        return self


@dataclass(frozen=True, slots=True)
class BrokerShadowEvidenceRequest:
    strategy_version: str
    execution_snapshot_sha256: str
    shadow_source_sha256: str
    shadow_trades: tuple[PaperTrade, ...]
    ledger: ReconciliationLedger
    account_activities: tuple[StoredPaperAccountActivity, ...]
    protective_oco_snapshots: tuple[StoredProtectiveOcoSnapshot, ...]
    reviewed_at: dt.datetime


class InvalidBrokerShadowEvidenceError(ValueError):
    def __str__(self) -> str:
        return "intraday broker/shadow evidence is invalid"


__all__ = (
    "BROKER_SHADOW_EVIDENCE_VERSION",
    "BrokerShadowAssessment",
    "BrokerShadowEvidence",
    "BrokerShadowEvidenceArtifact",
    "BrokerShadowEvidenceRequest",
    "BrokerShadowEvidenceStatus",
    "BrokerShadowMetrics",
    "BrokerShadowTradePair",
    "InvalidBrokerShadowEvidenceError",
)
