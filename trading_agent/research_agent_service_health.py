from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig

_HEALTH_FILENAME: Final = "research-agent-runtime-health.json"
_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
_HEALTH_CHECK_RETRIES: Final = 20
_HEALTH_CHECK_INTERVAL_SECONDS: Final = 0.25

HealthEvaluator = Callable[
    [ResearchAgentServiceConfig, dt.datetime, dt.datetime],
    "ResearchAgentServiceHealthEvaluation",
]
Clock = Callable[[], dt.datetime]


class InvalidResearchAgentServiceHealthError(RuntimeError):
    __slots__ = ("reason",)

    reason: Literal["health_read_invalid", "health_write_invalid"]

    def __init__(self, *, reason: Literal["health_read_invalid", "health_write_invalid"]) -> None:
        self.reason = reason
        super().__init__(reason)


class ResearchAgentServiceHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_at: AwareDatetime
    state: Literal["ready", "failed"]
    reason: Literal["runtime_ready", "runtime_failed"]

    @model_validator(mode="after")
    def require_state_reason_binding(self) -> ResearchAgentServiceHealth:
        match self.state, self.reason:
            case "ready", "runtime_ready":
                return self
            case "failed", "runtime_failed":
                return self
            case ("ready", "runtime_failed") | ("failed", "runtime_ready"):
                raise PydanticCustomError(
                    "research_agent_health_state_reason_invalid",
                    "research agent health state and reason are incompatible",
                )
            case unreachable:
                assert_never(unreachable)


class ResearchAgentServiceHealthEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    accepted: bool
    state: Literal["healthy", "unhealthy"]
    reason: Literal[
        "fresh_matching_ready",
        "report_missing_or_invalid",
        "candidate_mismatch",
        "not_fresh",
        "observed_in_future",
        "runtime_failed",
    ]
    health: ResearchAgentServiceHealth | None


def research_agent_service_health_path(output_root: Path) -> Path:
    return output_root / _HEALTH_FILENAME


def write_persisted_research_agent_service_health(
    output_root: Path,
    health: ResearchAgentServiceHealth,
) -> None:
    try:
        write_private_stable_report(
            research_agent_service_health_path(output_root),
            health.model_dump_json() + "\n",
        )
    except (InvalidPrivateStableReportError, OSError, TypeError, ValueError):
        raise InvalidResearchAgentServiceHealthError(reason="health_write_invalid") from None


def read_persisted_research_agent_service_health(output_root: Path) -> ResearchAgentServiceHealth:
    try:
        payload = read_private_text_query_only(research_agent_service_health_path(output_root))
        health = ResearchAgentServiceHealth.model_validate_json(payload)
        if payload != health.model_dump_json() + "\n":
            raise InvalidResearchAgentServiceHealthError(reason="health_read_invalid")
        return health
    except (
        InvalidPrivateQueryFileError,
        InvalidResearchAgentServiceHealthError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidResearchAgentServiceHealthError(reason="health_read_invalid") from None


def evaluate_persisted_research_agent_service_health(
    output_root: Path,
    expected_config_sha256: str,
    started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> ResearchAgentServiceHealthEvaluation:
    try:
        health = read_persisted_research_agent_service_health(output_root)
    except InvalidResearchAgentServiceHealthError:
        return ResearchAgentServiceHealthEvaluation(
            accepted=False,
            state="unhealthy",
            reason="report_missing_or_invalid",
            health=None,
        )
    match health:
        case ResearchAgentServiceHealth(config_sha256=config_sha256) if config_sha256 != expected_config_sha256:
            return _unhealthy_health("candidate_mismatch", health)
        case ResearchAgentServiceHealth(observed_at=observed_at) if observed_at <= started_at:
            return _unhealthy_health("not_fresh", health)
        case ResearchAgentServiceHealth(observed_at=observed_at) if observed_at > evaluated_at:
            return _unhealthy_health("observed_in_future", health)
        case ResearchAgentServiceHealth(state="failed"):
            return _unhealthy_health("runtime_failed", health)
        case ResearchAgentServiceHealth(state="ready"):
            return ResearchAgentServiceHealthEvaluation(
                accepted=True,
                state="healthy",
                reason="fresh_matching_ready",
                health=health,
            )
        case unreachable:
            raise AssertionError(unreachable)


def await_fresh_research_agent_service_health(
    candidate: ResearchAgentServiceConfig,
    started_at: dt.datetime,
    clock: Clock,
    health_evaluator: HealthEvaluator,
) -> ResearchAgentServiceHealthEvaluation:
    health = health_evaluator(candidate, started_at, clock())
    for _ in range(_HEALTH_CHECK_RETRIES):
        if health.accepted:
            return health
        time.sleep(_HEALTH_CHECK_INTERVAL_SECONDS)
        health = health_evaluator(candidate, started_at, clock())
    return health


def health_for_service_report(
    config_sha256: str,
    observed_at: dt.datetime,
    failed: bool,
) -> ResearchAgentServiceHealth:
    if failed:
        return ResearchAgentServiceHealth(
            config_sha256=config_sha256,
            observed_at=observed_at,
            state="failed",
            reason="runtime_failed",
        )
    return ResearchAgentServiceHealth(
        config_sha256=config_sha256,
        observed_at=observed_at,
        state="ready",
        reason="runtime_ready",
    )


def _unhealthy_health(
    reason: Literal["candidate_mismatch", "not_fresh", "observed_in_future", "runtime_failed"],
    health: ResearchAgentServiceHealth,
) -> ResearchAgentServiceHealthEvaluation:
    return ResearchAgentServiceHealthEvaluation(
        accepted=False,
        state="unhealthy",
        reason=reason,
        health=health,
    )


__all__ = (
    "HealthEvaluator",
    "InvalidResearchAgentServiceHealthError",
    "ResearchAgentServiceHealth",
    "ResearchAgentServiceHealthEvaluation",
    "await_fresh_research_agent_service_health",
    "evaluate_persisted_research_agent_service_health",
    "health_for_service_report",
    "read_persisted_research_agent_service_health",
    "research_agent_service_health_path",
    "write_persisted_research_agent_service_health",
)
