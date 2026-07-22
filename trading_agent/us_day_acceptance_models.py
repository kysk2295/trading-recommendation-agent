from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from trading_agent.acceptance_evidence import AcceptanceArtifactEvidence, AcceptanceSessionKind
from trading_agent.us_day_operating_models import UsDayOperatingTransition
from trading_agent.us_equity_calendar import regular_session_bounds

US_DAY_POLICY_VERSION = "us-day-operating-v1"
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class UsDayTerminalStatus(StrEnum):
    COMPLETED = "completed"
    CENSORED = "censored"
    BLOCKED = "blocked"
    INCIDENT = "incident"


class InvalidUsDayAcceptanceEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day acceptance evidence is invalid"


class UsDaySessionTerminal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    policy_version: Literal["us-day-operating-v1"] = US_DAY_POLICY_VERSION
    commit_sha: str
    session_id: str
    strategy_version: str
    session_kind: AcceptanceSessionKind
    fixture_label: str
    status: UsDayTerminalStatus
    reasons: tuple[str, ...]
    observed_from: AwareDatetime
    observed_through: AwareDatetime
    transitions: tuple[UsDayOperatingTransition, ...]
    open_order_count: int
    position_count: int
    protective_oco_count: int
    reconciliation_passed: bool
    broker_shadow_ledger_equal: bool
    outcome_delivery_id: str
    hermes_acknowledged: bool
    source_artifacts: tuple[AcceptanceArtifactEvidence, ...]

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        session_date = _session_date(self.session_id)
        noncompleted = self.status is not UsDayTerminalStatus.COMPLETED
        if (
            _GIT_SHA.fullmatch(self.commit_sha) is None
            or _IDENTIFIER.fullmatch(self.strategy_version) is None
            or _IDENTIFIER.fullmatch(self.fixture_label) is None
            or _HEX64.fullmatch(self.outcome_delivery_id) is None
            or self.observed_through < self.observed_from
            or self.observed_from.date() != session_date
            or any(value < 0 for value in self.final_counts)
            or not self.source_artifacts
            or len({item.path for item in self.source_artifacts}) != len(self.source_artifacts)
            or (self.session_kind is AcceptanceSessionKind.REAL) != (self.fixture_label == "real_session")
            or noncompleted != bool(self.reasons)
        ):
            raise InvalidUsDayAcceptanceEvidenceError
        return self

    @property
    def final_counts(self) -> tuple[int, int, int]:
        return self.open_order_count, self.position_count, self.protective_oco_count

    @property
    def is_real_scheduled_session(self) -> bool:
        return (
            self.session_kind is AcceptanceSessionKind.REAL
            and regular_session_bounds(_session_date(self.session_id)) is not None
        )

    @property
    def has_natural_lifecycle(self) -> bool:
        required = {
            UsDayOperatingTransition.ACTIONABLE,
            UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
            UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
            UsDayOperatingTransition.FLAT,
            UsDayOperatingTransition.RECONCILED,
            UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
        }
        return self.status is UsDayTerminalStatus.COMPLETED and required <= set(self.transitions)

    @property
    def is_finally_reconciled(self) -> bool:
        return (
            self.final_counts == (0, 0, 0)
            and self.reconciliation_passed
            and self.broker_shadow_ledger_equal
            and UsDayOperatingTransition.FLAT in self.transitions
            and UsDayOperatingTransition.RECONCILED in self.transitions
        )


class UsDayEvidenceEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    policy_version: Literal["us-day-operating-v1"] = US_DAY_POLICY_VERSION
    commit_sha: str
    generated_at: AwareDatetime
    session_ids: tuple[str, ...]
    fixture_labels: tuple[str, ...]
    source_artifact_hashes: tuple[str, ...]


class UsDayThreeSessionReport(UsDayEvidenceEnvelope):
    daily_terminal_count: int
    eligible_session_count: int
    delivery_subgate_passed: bool
    natural_paper_lifecycle_passed: bool
    final_reconciliation_passed: bool
    operating_product_complete: bool


class UsDayNaturalPaperLifecycleEvidence(UsDayEvidenceEnvelope):
    passed: bool
    qualifying_session_ids: tuple[str, ...]


class UsDayFinalReconciliationEvidence(UsDayEvidenceEnvelope):
    passed: bool
    failed_session_ids: tuple[str, ...]


class UsDayHermesOutcomeReceiptEvidence(UsDayEvidenceEnvelope):
    passed: bool
    delivery_ids: tuple[str, ...]


class UsDayAcceptanceBuildRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repository: Path
    terminal_paths: tuple[Path, ...]
    generated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not self.terminal_paths
            or len(set(self.terminal_paths)) != len(self.terminal_paths)
            or any(path.is_absolute() or path == Path() or ".." in path.parts for path in self.terminal_paths)
        ):
            raise InvalidUsDayAcceptanceEvidenceError
        return self


def _session_date(session_id: str) -> dt.date:
    try:
        session_date = dt.date.fromisoformat(session_id[-10:])
    except ValueError:
        raise InvalidUsDayAcceptanceEvidenceError from None
    if session_id != f"XNYS-{session_date.isoformat()}":
        raise InvalidUsDayAcceptanceEvidenceError
    return session_date
