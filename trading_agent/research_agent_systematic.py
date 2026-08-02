from __future__ import annotations

import datetime as dt
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self, assert_never, final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from run_autonomous_research_cycle import (
    AutonomousCycleCliResult,
    InvalidAutonomousCycleCliResultError,
    load_autonomous_cycle_cli_result,
)
from trading_agent.dashboard_executable_binding import (
    FileIdentity,
    InvalidExecutableBindingError,
    capture_file,
    revalidate,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
    research_agent_result_id,
)


class InvalidSystematicResearchActionError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class SystematicResearchActionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    project_root: Path
    uv_executable: Path
    python_executable: Path
    context: Path
    response_fixture: Path | None
    hermes_executable: Path | None
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    experiment_ledger: Path
    receipt_root: Path
    strategy_root: Path
    manifest_root: Path
    queue_root: Path
    input_csv: Path
    data_foundation_manifest: Path
    artifact_root: Path
    review_root: Path
    runs_root: Path
    max_runtime_seconds: float = Field(gt=0, le=3_600)
    max_bars: int = Field(default=100_000, ge=1, le=100_000)
    max_sessions: int = Field(default=60, ge=1, le=60)
    rss_limit_gib: float = Field(default=9.5, gt=0, le=10.0)

    @model_validator(mode="after")
    def require_absolute_provider_binding(self) -> Self:
        paths = (
            self.project_root,
            self.uv_executable,
            self.python_executable,
            self.context,
            self.experiment_ledger,
            self.receipt_root,
            self.strategy_root,
            self.manifest_root,
            self.queue_root,
            self.input_csv,
            self.data_foundation_manifest,
            self.artifact_root,
            self.review_root,
            self.runs_root,
        )
        if any(not path.is_absolute() for path in paths):
            raise InvalidSystematicResearchActionError(reason="systematic_path_not_absolute")
        if self.response_fixture is not None and not self.response_fixture.is_absolute():
            raise InvalidSystematicResearchActionError(reason="systematic_path_not_absolute")
        if self.hermes_executable is not None and not self.hermes_executable.is_absolute():
            raise InvalidSystematicResearchActionError(reason="systematic_path_not_absolute")
        if (self.response_fixture is None) == (self.hermes_executable is None):
            raise InvalidSystematicResearchActionError(reason="systematic_provider_binding_invalid")
        return self


@final
class SystematicResearchActionExecutor:
    __slots__ = ("_clock", "_config", "_script", "_uv")

    _clock: Callable[[], dt.datetime]
    _config: SystematicResearchActionConfig
    _script: FileIdentity
    _uv: FileIdentity

    def __init__(
        self,
        config: SystematicResearchActionConfig,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self._config = config
        self._clock = clock
        try:
            self._uv = capture_file(config.uv_executable, executable=True)
            self._script = capture_file(config.project_root / "run_autonomous_research_cycle.py", executable=False)
        except InvalidExecutableBindingError:
            raise InvalidSystematicResearchActionError(reason="systematic_executable_binding_invalid") from None

    def execute(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ResearchAgentResultV1:
        command = systematic_cycle_command(self._config, cycle)
        try:
            revalidate(self._uv, executable=True)
            revalidate(self._script, executable=False)
            completed = subprocess.run(
                command,
                cwd=self._config.project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self._config.max_runtime_seconds,
                env={"PATH": "/usr/bin:/bin"},
            )
            report = load_autonomous_cycle_cli_result(_cycle_output(self._config, cycle))
        except (
            InvalidAutonomousCycleCliResultError,
            InvalidExecutableBindingError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ):
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None
        if (completed.returncode, report.status) not in {(0, "complete"), (1, "blocked")}:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed")
        return _result_from_report(SystematicResultContext(cycle, decision, report, self._clock()))


def systematic_cycle_command(
    config: SystematicResearchActionConfig,
    cycle: ResearchAgentCycleV1,
) -> tuple[str, ...]:
    provider = (
        ("--response-fixture", str(config.response_fixture))
        if config.response_fixture is not None
        else ("--hermes-executable", str(config.hermes_executable), "--model-id", config.model_id)
    )
    return (
        str(config.uv_executable),
        "run",
        "--offline",
        "python",
        str(config.project_root / "run_autonomous_research_cycle.py"),
        "--context",
        str(config.context),
        *provider,
        "--experiment-ledger",
        str(config.experiment_ledger),
        "--receipt-root",
        str(config.receipt_root),
        "--strategy-root",
        str(config.strategy_root),
        "--manifest-root",
        str(config.manifest_root),
        "--queue-root",
        str(config.queue_root),
        "--input-csv",
        str(config.input_csv),
        "--data-foundation-manifest",
        str(config.data_foundation_manifest),
        "--artifact-root",
        str(config.artifact_root),
        "--review-root",
        str(config.review_root),
        "--output-dir",
        str(_cycle_output(config, cycle)),
        "--python-executable",
        str(config.python_executable),
        "--max-bars",
        str(config.max_bars),
        "--max-sessions",
        str(config.max_sessions),
        "--rss-limit-gib",
        str(config.rss_limit_gib),
    )


def _cycle_output(config: SystematicResearchActionConfig, cycle: ResearchAgentCycleV1) -> Path:
    return config.runs_root / cycle.cycle_id / "output"


@dataclass(frozen=True, slots=True)
class SystematicResultContext:
    cycle: ResearchAgentCycleV1
    decision: ResearchAgentDecisionV1
    report: AutonomousCycleCliResult
    occurred_at: dt.datetime


def _result_from_report(context: SystematicResultContext) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    report = context.report
    occurred_at = context.occurred_at
    match report.status:
        case "blocked":
            return ResearchAgentResultV1(
                result_id=research_agent_result_id(cycle.cycle_id),
                cycle_id=cycle.cycle_id,
                agent_family_id=cycle.agent_family_id,
                market_id=cycle.market_id,
                status=ResearchAgentResultStatus.FAILED,
                question=decision.question,
                summary="The bounded generated strategy cycle was blocked.",
                reason=report.reason_codes[0],
                continuation="Retry the same evidence after the fixed failure backoff.",
                evidence_refs=decision.evidence_refs,
                artifact_refs=(),
                occurred_at=occurred_at,
                next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
                next_wake_at=occurred_at + dt.timedelta(minutes=15),
            )
        case "complete":
            artifacts = _complete_artifacts(report)
            return ResearchAgentResultV1(
                result_id=research_agent_result_id(cycle.cycle_id),
                cycle_id=cycle.cycle_id,
                agent_family_id=cycle.agent_family_id,
                market_id=cycle.market_id,
                status=ResearchAgentResultStatus.COMPLETED,
                question=decision.question,
                summary="The generated strategy cycle completed under the deterministic Reviewer.",
                reason=f"reviewer_{report.reviewer_decision}",
                continuation=None,
                evidence_refs=decision.evidence_refs,
                artifact_refs=artifacts,
                occurred_at=occurred_at,
                next_wake_kind=decision.next_wake_kind,
                next_wake_at=decision.next_wake_at,
            )
        case unreachable:
            assert_never(unreachable)


def _complete_artifacts(report: AutonomousCycleCliResult) -> tuple[str, ...]:
    values = (
        report.strategy_artifact_id,
        report.trial_id,
        report.experiment_artifact_id,
        report.review_artifact_id,
    )
    if any(value is None for value in values):
        raise InvalidSystematicResearchActionError(reason="systematic_complete_artifact_missing")
    return tuple(sorted(value for value in values if value is not None))


__all__ = (
    "InvalidSystematicResearchActionError",
    "SystematicResearchActionConfig",
    "SystematicResearchActionExecutor",
    "systematic_cycle_command",
)
