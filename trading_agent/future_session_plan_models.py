from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FutureSessionMarket(StrEnum):
    US = "us"
    KR = "kr"


class FutureSessionPlanStatus(StrEnum):
    READY_TO_PREPARE = "ready_to_prepare"
    WAITING_AUTHORITY = "waiting_authority"


class DeferredTrialRegistrationState(StrEnum):
    DEFERRED_UNTIL_PREOPEN = "deferred_until_preopen"


class WaitingAuthorityReason(StrEnum):
    CALENDAR_AUTHORITY_MISSING = "calendar_authority_missing"
    CALENDAR_AUTHORITY_INVALID = "calendar_authority_invalid"
    FROZEN_RUNTIME_INVALID = "frozen_runtime_invalid"
    RUNTIME_AUTHORITY_MISSING = "runtime_authority_missing"
    RUNTIME_AUTHORITY_AMBIGUOUS = "runtime_authority_ambiguous"
    SCHEDULER_AUTHORITY_INVALID = "scheduler_authority_invalid"
    ROLLOVER_BUNDLE_INVALID = "rollover_bundle_invalid"
    ROLLOVER_BUNDLE_MISMATCH = "rollover_bundle_mismatch"
    TRIAL_AUTHORITY_CONFLICT = "trial_authority_conflict"


class FrozenRuntimeAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    directory: Path
    commit_sha: str

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if not self.directory.is_absolute() or _COMMIT_SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("invalid frozen runtime authority")
        return self


class FutureSessionArtifactLayout(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    jobs_dir: Path
    receipts_dir: Path
    logs_dir: Path
    reports_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> Self:
        if not root.is_absolute():
            raise ValueError("artifact root must be absolute")
        return cls(
            root=root,
            jobs_dir=root / "jobs",
            receipts_dir=root / "receipts",
            logs_dir=root / "logs",
            reports_dir=root / "reports",
        )

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        if (
            not self.root.is_absolute()
            or self.jobs_dir != self.root / "jobs"
            or self.receipts_dir != self.root / "receipts"
            or self.logs_dir != self.root / "logs"
            or self.reports_dir != self.root / "reports"
        ):
            raise ValueError("artifact layout must be canonical")
        return self


class FutureSessionPlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    market: FutureSessionMarket
    after_date: dt.date
    compiled_at: dt.datetime
    scheduler_main_sha: str
    authority_repository: Path
    frozen_runtime: FrozenRuntimeAuthority
    artifact_root: Path
    experiment_ledger: Path
    lane_registry: Path | None = None
    execution_database: Path | None = None
    required_runtime_commits: tuple[str, ...] = ()
    kr_calendar_store: Path | None = None
    kr_rollover_bundle: Path | None = None
    cycles: int = 390
    interval_seconds: int = 60
    kis_server_attempts: int = 4
    eod_last_bar_semantic_attempts: int = 3

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        paths = (
            self.authority_repository,
            self.artifact_root,
            self.experiment_ledger,
            *(() if self.lane_registry is None else (self.lane_registry,)),
            *(() if self.execution_database is None else (self.execution_database,)),
            *(() if self.kr_calendar_store is None else (self.kr_calendar_store,)),
            *(() if self.kr_rollover_bundle is None else (self.kr_rollover_bundle,)),
        )
        commits = (self.scheduler_main_sha, *self.required_runtime_commits)
        if (
            not _aware(self.compiled_at)
            or any(_COMMIT_SHA.fullmatch(value) is None for value in commits)
            or any(not path.is_absolute() for path in paths)
        ):
            raise ValueError("invalid future-session request")
        match self.market:
            case FutureSessionMarket.US:
                valid_shape = (
                    self.lane_registry is not None
                    and self.execution_database is not None
                    and self.kr_calendar_store is None
                    and self.kr_rollover_bundle is None
                )
            case FutureSessionMarket.KR:
                valid_shape = (
                    self.lane_registry is None
                    and self.execution_database is None
                    and self.kr_calendar_store is not None
                    and self.kr_rollover_bundle is not None
                )
        if not valid_shape:
            raise ValueError("market authority inputs are incomplete")
        return self


class StrategyRegistrationIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    lane_id: str
    operating_mode: str
    registration_sha256: str

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if not all(
            (
                self.strategy_version,
                self.code_version,
                self.lane_id,
                self.operating_mode,
            )
        ) or _SHA256.fullmatch(self.registration_sha256) is None:
            raise ValueError("invalid strategy registration identity")
        return self


class SessionCalendarProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    source_version: str
    evidence_sha256: str
    observed_at: dt.datetime | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if (
            not self.source
            or not self.source_version
            or _SHA256.fullmatch(self.evidence_sha256) is None
            or (
                self.observed_at is not None
                and not _aware(self.observed_at)
            )
        ):
            raise ValueError("invalid calendar provenance")
        return self


class JobTimingSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    run_at: dt.datetime
    purpose: str

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if not self.job_id or not self.purpose or not _aware(self.run_at):
            raise ValueError("invalid job timing")
        return self


class ReadyToPrepareSessionPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal[FutureSessionPlanStatus.READY_TO_PREPARE] = (
        FutureSessionPlanStatus.READY_TO_PREPARE
    )
    plan_sha256: str
    market: FutureSessionMarket
    target_session: dt.date
    compiled_at: dt.datetime
    scheduler_main_sha: str
    frozen_runtime: FrozenRuntimeAuthority
    calendar_provenance: SessionCalendarProvenance
    strategy_registrations: tuple[StrategyRegistrationIdentity, ...]
    kr_rollover_bundle_sha256: str | None
    kr_policy_sha256: str | None
    artifact_layout: FutureSessionArtifactLayout
    trial_registration_state: DeferredTrialRegistrationState
    jobs: tuple[JobTimingSpec, ...]

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        registration_ids = tuple(
            item.strategy_version for item in self.strategy_registrations
        )
        run_times = tuple(job.run_at for job in self.jobs)
        kr_hashes = (
            self.kr_rollover_bundle_sha256,
            self.kr_policy_sha256,
        )
        kr_shape = (
            all(value is None for value in kr_hashes)
            if self.market is FutureSessionMarket.US
            else all(
                value is not None and _SHA256.fullmatch(value) is not None
                for value in kr_hashes
            )
        )
        if (
            not self.strategy_registrations
            or registration_ids != tuple(sorted(set(registration_ids)))
            or not self.jobs
            or run_times != tuple(sorted(run_times))
            or not kr_shape
            or self.plan_sha256 != plan_content_sha256(self)
        ):
            raise ValueError("invalid ready future-session plan")
        return self


class WaitingSessionAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal[FutureSessionPlanStatus.WAITING_AUTHORITY] = (
        FutureSessionPlanStatus.WAITING_AUTHORITY
    )
    market: FutureSessionMarket
    target_session: dt.date | None
    compiled_at: dt.datetime
    scheduler_main_sha: str
    frozen_runtime: FrozenRuntimeAuthority
    reasons: tuple[WaitingAuthorityReason, ...]
    jobs: tuple[()] = ()

    @model_validator(mode="after")
    def validate_waiting(self) -> Self:
        if (
            not self.reasons
            or self.reasons
            != tuple(sorted(set(self.reasons), key=lambda reason: reason.value))
            or self.jobs
        ):
            raise ValueError("invalid waiting future-session authority")
        return self


FutureSessionPlanDecision = Annotated[
    ReadyToPrepareSessionPlan | WaitingSessionAuthority,
    Field(discriminator="status"),
]


def canonical_plan_json(
    value: ReadyToPrepareSessionPlan | WaitingSessionAuthority,
) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def plan_content_sha256(payload: BaseModel) -> str:
    return hashlib.sha256(
        json.dumps(
            payload.model_dump(mode="json", exclude={"plan_sha256"}),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "DeferredTrialRegistrationState",
    "FrozenRuntimeAuthority",
    "FutureSessionArtifactLayout",
    "FutureSessionMarket",
    "FutureSessionPlanDecision",
    "FutureSessionPlanRequest",
    "FutureSessionPlanStatus",
    "JobTimingSpec",
    "ReadyToPrepareSessionPlan",
    "SessionCalendarProvenance",
    "StrategyRegistrationIdentity",
    "WaitingAuthorityReason",
    "WaitingSessionAuthority",
    "canonical_plan_json",
    "plan_content_sha256",
)
