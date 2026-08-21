from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, Literal, Protocol, Self, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_SHA256_PATTERN: Final = r"^[a-f0-9]{64}$"


@unique
class UsDaySessionPhase(StrEnum):
    PREMARKET = "premarket"
    REGULAR = "regular"
    ENTRY_CUTOFF = "entry_cutoff"
    EOD = "eod"
    POST_CLOSE = "post_close"
    CLOSED = "closed"


class UsDayAgentServiceError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason: str = reason
        super().__init__(reason)


class UsDayAgentTickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    situation_path: Path
    evaluated_at: AwareDatetime
    source_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("evaluated_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

class UsDayAgentTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["accepted", "blocked"]
    phase: UsDaySessionPhase
    tick_id: str = Field(pattern=_SHA256_PATTERN)
    reason: str | None = Field(default=None, min_length=1, max_length=160)
    market_map_id: str | None = None
    recommendation_id: str | None = None
    paper_status: str | None = None
    paper_eligible: bool = False
    market_close_report_id: str | None = None
    challenger_version_id: str | None = None

    @model_validator(mode="after")
    def require_status_fields(self) -> Self:
        if (self.status == "blocked") != (self.reason is not None) or (
            self.paper_eligible and (self.status == "blocked" or self.recommendation_id is None)
        ):
            raise UsDayAgentServiceError("tick_result_status_invalid")
        return self

    @classmethod
    def accepted(
        cls,
        request: UsDayAgentTickRequest,
        *,
        market_map_id: str | None = None,
        recommendation_id: str | None = None,
        paper_status: str | None = None,
        paper_eligible: bool = False,
        market_close_report_id: str | None = None,
        challenger_version_id: str | None = None,
    ) -> Self:
        return cls(
            status="accepted",
            phase=session_phase_at(request.evaluated_at),
            tick_id=tick_id_for(request),
            market_map_id=market_map_id,
            recommendation_id=recommendation_id,
            paper_status=paper_status,
            paper_eligible=paper_eligible,
            market_close_report_id=market_close_report_id,
            challenger_version_id=challenger_version_id,
        )

    @classmethod
    def blocked(cls, request: UsDayAgentTickRequest, reason: str) -> Self:
        return cls(
            status="blocked",
            phase=session_phase_at(request.evaluated_at),
            tick_id=tick_id_for(request),
            reason=reason,
        )

    def compact(self) -> dict[str, str]:
        pairs = (
            ("status", self.status),
            ("phase", self.phase.value),
            ("reason", self.reason),
            ("market_map_id", self.market_map_id),
            ("recommendation_id", self.recommendation_id),
            ("paper_status", self.paper_status),
            ("market_close_report_id", self.market_close_report_id),
            ("challenger_version_id", self.challenger_version_id),
        )
        return {key: value for key, value in pairs if value is not None}


class UsDayHumanTraderVertical(Protocol):
    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def recover_paper(self, request: UsDayAgentTickRequest) -> None: ...

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def publish_regular(self, request: UsDayAgentTickRequest, result: UsDayAgentTickResult) -> None: ...

    def execute_paper(self, request: UsDayAgentTickRequest, result: UsDayAgentTickResult) -> str: ...

    def cutoff(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def eod(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def post_close(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...


@dataclass(frozen=True, slots=True)
class UsDayAgentServiceConfig:
    receipt_root: Path
    entry_cutoff_before_close: dt.timedelta = dt.timedelta(minutes=15)
    eod_before_close: dt.timedelta = dt.timedelta(minutes=5)

    def __post_init__(self) -> None:
        if not dt.timedelta() < self.eod_before_close < self.entry_cutoff_before_close < dt.timedelta(hours=1):
            raise UsDayAgentServiceError("session_configuration_invalid")


@dataclass(frozen=True, slots=True)
class UsDayAgentService:
    config: UsDayAgentServiceConfig
    vertical: UsDayHumanTraderVertical
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)

    def tick_from_source(self, situation_path: Path, source_sha256: str) -> UsDayAgentTickResult:
        return self.tick(
            UsDayAgentTickRequest(
                situation_path=situation_path,
                evaluated_at=self.clock(),
                source_sha256=source_sha256,
            )
        )

    def tick(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        replay = _read_receipt(self.config.receipt_root, request)
        if replay is not None:
            return replay
        phase = session_phase_at(
            request.evaluated_at,
            entry_cutoff_before_close=self.config.entry_cutoff_before_close,
            eod_before_close=self.config.eod_before_close,
        )
        match phase:
            case UsDaySessionPhase.PREMARKET:
                result = self.vertical.premarket(request)
            case UsDaySessionPhase.REGULAR:
                self.vertical.recover_paper(request)
                result = self.vertical.regular(request)
                if result.status == "accepted":
                    self.vertical.publish_regular(request, result)
                    if result.paper_eligible:
                        paper_status = self.vertical.execute_paper(request, result)
                        result = result.model_copy(update={"paper_status": paper_status})
            case UsDaySessionPhase.ENTRY_CUTOFF:
                self.vertical.recover_paper(request)
                result = self.vertical.cutoff(request)
            case UsDaySessionPhase.EOD:
                self.vertical.recover_paper(request)
                result = self.vertical.eod(request)
            case UsDaySessionPhase.POST_CLOSE:
                self.vertical.recover_paper(request)
                result = self.vertical.post_close(request)
            case UsDaySessionPhase.CLOSED:
                result = UsDayAgentTickResult.blocked(request, "xnys_session_closed")
            case unreachable:
                assert_never(unreachable)
        if result.phase is not phase or result.tick_id != tick_id_for(request):
            raise UsDayAgentServiceError("vertical_result_identity_invalid")
        return _publish_receipt(self.config.receipt_root, result)


def session_phase_at(
    evaluated_at: dt.datetime,
    *,
    entry_cutoff_before_close: dt.timedelta = dt.timedelta(minutes=15),
    eod_before_close: dt.timedelta = dt.timedelta(minutes=5),
) -> UsDaySessionPhase:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise UsDayAgentServiceError("aware_clock_required")
    local = evaluated_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    if bounds is None:
        return UsDaySessionPhase.CLOSED
    open_at, close_at = bounds
    if local < open_at:
        return UsDaySessionPhase.PREMARKET
    if local < close_at - entry_cutoff_before_close:
        return UsDaySessionPhase.REGULAR
    if local < close_at - eod_before_close:
        return UsDaySessionPhase.ENTRY_CUTOFF
    if local < close_at:
        return UsDaySessionPhase.EOD
    return UsDaySessionPhase.POST_CLOSE


def tick_id_for(request: UsDayAgentTickRequest) -> str:
    payload = json.dumps(
        {
            "evaluated_at": request.evaluated_at.isoformat(),
            "situation_path": str(request.situation_path.expanduser().absolute()),
            "source_sha256": request.source_sha256,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _receipt_path(root: Path, request: UsDayAgentTickRequest) -> Path:
    return root.expanduser().absolute() / f"{tick_id_for(request)}.json"


def _read_receipt(root: Path, request: UsDayAgentTickRequest) -> UsDayAgentTickResult | None:
    path = _receipt_path(root, request)
    if not path.exists():
        return None
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise UsDayAgentServiceError("tick_receipt_metadata_invalid")
        result = UsDayAgentTickResult.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError:
        raise UsDayAgentServiceError("tick_receipt_read_failed") from None
    if result.tick_id != tick_id_for(request):
        raise UsDayAgentServiceError("tick_receipt_identity_invalid")
    return result


def _publish_receipt(root: Path, result: UsDayAgentTickResult) -> UsDayAgentTickResult:
    directory = root.expanduser().absolute()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise UsDayAgentServiceError("tick_receipt_root_invalid")
        os.chmod(directory, 0o700)
        path = directory / f"{result.tick_id}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _ = stream.write(result.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        return result
    except FileExistsError:
        path = directory / f"{result.tick_id}.json"
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise UsDayAgentServiceError("tick_receipt_metadata_invalid") from None
        existing = UsDayAgentTickResult.model_validate_json(path.read_text(encoding="utf-8"))
        if existing != result:
            raise UsDayAgentServiceError("tick_receipt_conflict") from None
        return existing
    except OSError:
        raise UsDayAgentServiceError("tick_receipt_write_failed") from None
