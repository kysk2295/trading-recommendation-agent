from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import threading
from collections.abc import Callable
from typing import final

from run_autonomous_research_cycle import (
    REPORT_NAME,
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
    ResearchAgentResultV1,
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
from trading_agent.research_agent_systematic_models import (
    InvalidSystematicResearchActionError,
    SystematicFailureContext,
    SystematicResearchActionConfig,
    SystematicResultContext,
    failed_result,
    launched_result,
    pending_result,
    result_from_report,
)
from trading_agent.research_agent_systematic_supervision import (
    SystematicChildSupervisorConfig,
    process_group_rss_bytes,
    reap_systematic_child,
)


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
        if cycle.agent_family_id != "systematic_quant":
            raise InvalidSystematicResearchActionError(reason="systematic_family_identity_mismatch")
        if context.decision.primary_decision not in {
            ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT,
            ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
        }:
            raise InvalidSystematicResearchActionError(reason="systematic_action_invalid")
        pending = self._pending_request()
        if pending is not None:
            return self._review_request(context, pending)
        if context.decision.primary_decision is ResearchAgentDecisionKind.REVIEW_OPEN_STATE:
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
            target=reap_systematic_child,
            args=(
                process,
                SystematicChildSupervisorConfig(
                    output=self._config.runs_root / cycle.cycle_id / "output",
                    max_runtime_seconds=self._config.max_runtime_seconds,
                    rss_limit_gib=self._config.rss_limit_gib,
                ),
                process_group_rss_bytes,
            ),
            name="systematic-child-reaper",
            daemon=True,
        ).start()
        return launched_result(context, request_sha)

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
        if not (output / REPORT_NAME).exists():
            if context.observed_at <= pending.occurred_at + dt.timedelta(seconds=self._config.max_runtime_seconds + 30):
                return pending_result(context, pending.open_work_ref)
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed")
        try:
            report = load_autonomous_cycle_cli_result(output)
        except InvalidAutonomousCycleCliResultError:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None
        result = result_from_report(
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
            return failed_result(
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
        self._revalidate_executables()
        try:
            ready = resolve_ready_systematic_input(self._config.input_activation)
        except (
            InvalidSystematicInputActivationError,
            OSError,
            SystematicInputEvidenceError,
            TypeError,
            ValueError,
        ):
            return failed_result(
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
            report = load_autonomous_cycle_cli_result(self._config.runs_root / cycle.cycle_id / "output")
        except (
            InvalidAutonomousCycleCliResultError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ):
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed") from None
        if (completed.returncode, report.status) not in {(0, "complete"), (1, "blocked")}:
            raise InvalidSystematicResearchActionError(reason="systematic_cycle_execution_failed")
        return result_from_report(SystematicResultContext(cycle, decision, report, self._clock()))
