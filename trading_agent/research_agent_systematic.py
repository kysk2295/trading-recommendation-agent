from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Self, assert_never, final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from run_autonomous_research_cycle import (
    REPORT_NAME,
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
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_systematic_input_evidence import SystematicInputEvidenceError
from trading_agent.research_agent_systematic_input_models import ReadySystematicInputActivation
from trading_agent.research_agent_systematic_input_runtime import (
    resolve_ready_systematic_input,
    systematic_cycle_command,
)
from trading_agent.research_agent_systematic_input_store import (
    InvalidSystematicInputActivationError,
)


class InvalidSystematicResearchActionError(RuntimeError):
    __slots__ = ("reason",)

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
    provider_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    experiment_ledger: Path
    receipt_root: Path
    strategy_root: Path
    manifest_root: Path
    queue_root: Path
    input_activation: Path
    artifact_root: Path
    review_root: Path
    runs_root: Path
    max_runtime_seconds: float = Field(gt=0, le=3_600)
    max_bars: int = Field(default=100_000, ge=1, le=100_000)
    max_sessions: int = Field(default=60, ge=1, le=60)
    rss_limit_gib: float = Field(default=9.5, gt=0, le=9.5)

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
            self.input_activation,
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
    __slots__ = ("_clock", "_config", "_prior_results", "_script", "_uv")

    _clock: Callable[[], dt.datetime]
    _config: SystematicResearchActionConfig
    _script: FileIdentity
    _uv: FileIdentity
    _prior_results: Callable[[], tuple[ResearchAgentResultV1, ...]]

    def __init__(
        self,
        config: SystematicResearchActionConfig,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
        prior_results: Callable[[], tuple[ResearchAgentResultV1, ...]] = lambda: (),
    ) -> None:
        self._config = config
        self._clock = clock
        self._prior_results = prior_results
        try:
            self._uv = capture_file(config.uv_executable, executable=True)
            self._script = capture_file(config.project_root / "run_autonomous_research_cycle.py", executable=False)
        except InvalidExecutableBindingError:
            raise InvalidSystematicResearchActionError(reason="systematic_executable_binding_invalid") from None

    def execute_context(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        cycle = context.cycle
        decision = context.decision
        if cycle.agent_family_id != "systematic_quant":
            raise InvalidSystematicResearchActionError(reason="systematic_family_identity_mismatch")
        if decision.primary_decision not in {
            ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT,
            ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
        }:
            raise InvalidSystematicResearchActionError(reason="systematic_action_invalid")
        pending = self._pending_request()
        if pending is not None:
            return self._review_request(context, pending)
        if decision.primary_decision is ResearchAgentDecisionKind.REVIEW_OPEN_STATE:
            raise InvalidSystematicResearchActionError(reason="systematic_open_work_unresolved")
        return self._launch_request(context)

    def _launch_request(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        cycle = context.cycle
        decision = context.decision
        self._revalidate_executables()
        ready = self._ready_input(cycle, decision)
        if isinstance(ready, ResearchAgentResultV1):
            return ready
        command = systematic_cycle_command(self._config, cycle, ready)
        request_payload = json.dumps(
            {
                "command_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
                "cycle_id": cycle.cycle_id,
                "evidence_refs": decision.evidence_refs,
                "launched_at": context.observed_at.isoformat(),
                "schema_version": 1,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        request_sha = hashlib.sha256(request_payload.encode()).hexdigest()
        try:
            write_private_stable_report(
                self._config.runs_root / cycle.cycle_id / "request.json",
                request_payload + "\n",
            )
            process = subprocess.Popen(
                command,
                cwd=self._config.project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin"},
                start_new_session=True,
            )
        except (InvalidPrivateStableReportError, OSError, subprocess.SubprocessError, ValueError):
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_launch_failed") from None
        threading.Thread(
            target=_reap_child,
            args=(process, _cycle_output(self._config, cycle), self._config.max_runtime_seconds),
            name="systematic-child-reaper",
            daemon=True,
        ).start()
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(cycle.cycle_id),
            cycle_id=cycle.cycle_id,
            agent_family_id=cycle.agent_family_id,
            market_id=cycle.market_id,
            status=ResearchAgentResultStatus.COMPLETED,
            question=decision.question,
            summary="The bounded generated strategy experiment was launched outside the fast actor loop.",
            reason="review_pending",
            continuation="Review the immutable experiment and Reviewer report at the scheduled wake.",
            open_work_ref=f"systematic.run.{cycle.cycle_id}",
            evidence_refs=decision.evidence_refs,
            artifact_refs=(f"systematic_request.{request_sha}",),
            occurred_at=context.observed_at,
            next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
            next_wake_at=context.observed_at + dt.timedelta(seconds=30),
        )

    def _review_request(
        self,
        context: ResearchAgentActionContext,
        pending: ResearchAgentResultV1,
    ) -> ResearchAgentResultV1:
        work_ref = pending.open_work_ref or ""
        request_cycle_id = work_ref.removeprefix("systematic.run.")
        if len(request_cycle_id) != 64 or any(character not in "0123456789abcdef" for character in request_cycle_id):
            raise InvalidSystematicResearchActionError(reason="systematic_open_work_unresolved")
        output = self._config.runs_root / request_cycle_id / "output"
        report_path = output / REPORT_NAME
        if not report_path.exists():
            if context.observed_at <= pending.occurred_at + dt.timedelta(seconds=self._config.max_runtime_seconds + 30):
                return _pending_result(context, pending.open_work_ref)
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed")
        try:
            report = load_autonomous_cycle_cli_result(output)
        except InvalidAutonomousCycleCliResultError:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None
        result = _result_from_report(
            SystematicResultContext(context.cycle, context.decision, report, context.observed_at)
        )
        return result.model_copy(update={"open_work_ref": pending.open_work_ref})

    def _pending_request(self) -> ResearchAgentResultV1 | None:
        for result in reversed(self._prior_results()):
            work = result.open_work_ref
            if result.agent_family_id != "systematic_quant" or work is None or not work.startswith("systematic.run."):
                continue
            return result if result.reason in {"review_pending", "systematic_run_pending"} else None
        return None

    def _revalidate_executables(self) -> None:
        try:
            revalidate(self._uv, executable=True)
            revalidate(self._script, executable=False)
        except InvalidExecutableBindingError:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None

    def _ready_input(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ReadySystematicInputActivation | ResearchAgentResultV1:
        try:
            return resolve_ready_systematic_input(self._config.input_activation)
        except (
            InvalidSystematicInputActivationError,
            OSError,
            SystematicInputEvidenceError,
            TypeError,
            ValueError,
        ):
            return _failed_result(
                SystematicFailureContext(
                    cycle=cycle,
                    decision=decision,
                    occurred_at=self._clock(),
                    reason="production_input_unavailable",
                    summary="The production Systematic input is unavailable.",
                    continuation="Retry after a verified production input activation is available.",
                )
            )

    def execute(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ResearchAgentResultV1:
        try:
            revalidate(self._uv, executable=True)
            revalidate(self._script, executable=False)
        except InvalidExecutableBindingError:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None
        try:
            ready = resolve_ready_systematic_input(self._config.input_activation)
        except (
            InvalidSystematicInputActivationError,
            OSError,
            SystematicInputEvidenceError,
            TypeError,
            ValueError,
        ):
            return _failed_result(
                SystematicFailureContext(
                    cycle=cycle,
                    decision=decision,
                    occurred_at=self._clock(),
                    reason="production_input_unavailable",
                    summary="The production Systematic input is unavailable.",
                    continuation="Retry after a verified production input activation is available.",
                )
            )
        command = systematic_cycle_command(self._config, cycle, ready)
        try:
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
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ):
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None
        if (completed.returncode, report.status) not in {(0, "complete"), (1, "blocked")}:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed")
        return _result_from_report(SystematicResultContext(cycle, decision, report, self._clock()))


def _cycle_output(config: SystematicResearchActionConfig, cycle: ResearchAgentCycleV1) -> Path:
    return config.runs_root / cycle.cycle_id / "output"


def _reap_child(
    process: subprocess.Popen[bytes],
    output: Path,
    max_runtime_seconds: float,
) -> None:
    reason: str | None = None
    try:
        return_code = process.wait(timeout=max_runtime_seconds)
        if return_code not in {0, 1}:
            reason = "systematic_child_failed"
    except subprocess.TimeoutExpired:
        reason = "systematic_child_timeout"
        try:
            os.killpg(process.pid, signal.SIGTERM)
            _ = process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            with suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                _ = process.wait(timeout=5)
    if reason is not None and not (output / REPORT_NAME).exists():
        with suppress(InvalidPrivateStableReportError):
            write_private_stable_report(output / REPORT_NAME, _blocked_child_report(reason))


def _blocked_child_report(reason: str) -> str:
    return "\n".join(
        (
            "# Autonomous generated strategy research cycle",
            "",
            "- result: blocked",
            f"- {reason}",
            "- lifecycle authority: false",
            "- allocation authority: false",
            "- order authority: false",
            "- trading mutation: 0",
            "",
        )
    )


@dataclass(frozen=True, slots=True)
class SystematicResultContext:
    cycle: ResearchAgentCycleV1
    decision: ResearchAgentDecisionV1
    report: AutonomousCycleCliResult
    occurred_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SystematicFailureContext:
    cycle: ResearchAgentCycleV1
    decision: ResearchAgentDecisionV1
    occurred_at: dt.datetime
    reason: str
    summary: str
    continuation: str


def _result_from_report(context: SystematicResultContext) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    report = context.report
    occurred_at = context.occurred_at
    match report.status:
        case "blocked":
            return _failed_result(
                SystematicFailureContext(
                    cycle=cycle,
                    decision=decision,
                    occurred_at=occurred_at,
                    reason=report.reason_codes[0],
                    summary="The bounded generated strategy cycle was blocked.",
                    continuation="Retry the same evidence after the fixed failure backoff.",
                )
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


def _failed_result(context: SystematicFailureContext) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=ResearchAgentResultStatus.FAILED,
        question=decision.question,
        summary=context.summary,
        reason=context.reason,
        continuation=context.continuation,
        evidence_refs=decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.occurred_at,
        next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
        next_wake_at=context.occurred_at + dt.timedelta(minutes=15),
    )


def _pending_result(
    context: ResearchAgentActionContext,
    open_work_ref: str | None,
) -> ResearchAgentResultV1:
    if open_work_ref is None:
        raise InvalidSystematicResearchActionError(reason="systematic_open_work_unresolved")
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="systematic_quant",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="The generated strategy child is still running outside the fast actor loop.",
        reason="systematic_run_pending",
        continuation="Poll the same immutable Systematic request at the next scheduled wake.",
        open_work_ref=open_work_ref,
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
        next_wake_at=context.observed_at + dt.timedelta(seconds=30),
    )


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
