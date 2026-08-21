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

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices, run_loop_engineer
from trading_agent.day_agent_reasoning import DayAgentReasoningClient
from trading_agent.day_agent_runtime import DayAgentRuntime, run_day_agent_task
from trading_agent.day_agent_task_models import DayAgentBudget, DayAgentResearchTask, DayAgentTaskState
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_runtime import DayAgentToolRuntime
from trading_agent.day_agent_version_models import AgentDeploymentState, AgentVersion
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import MarketCloseReportPayload
from trading_agent.day_learning_report_store import publish_market_close_report
from trading_agent.day_learning_reports import seal_market_close_report
from trading_agent.day_research_review_models import ExecutionEligibility, PromotionDecision
from trading_agent.lane_identity_models import LaneId
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text
from trading_agent.research_identity_models import StrategyLaneRef
from trading_agent.store import PaperStore
from trading_agent.us_day_agent_operating import (
    UsDayAgentOperatingRequest,
    UsDayAgentOperatingServices,
    operate_us_day_agent,
)
from trading_agent.us_day_recommendation_card import persist_and_queue_thesis
from trading_agent.us_day_signal_admission import UsDaySignalAdmissionRequest
from trading_agent.us_day_situation_models import UsDaySituationMap
from trading_agent.us_day_thesis_models import (
    UsDayChampion,
    UsDayCurrentMarket,
    UsDayPlaybook,
    situation_id_for,
)
from trading_agent.us_day_thesis_runtime import (
    InvalidUsDayThesisError,
    Reasoner,
    UsDayThesisResult,
    generate_trade_thesis,
)
from trading_agent.us_day_thesis_store import UsDayThesisStore
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

# SIZE_OK: the plan assigns one composition root that makes cross-module authority order reviewable.
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


class CanonicalUsDaySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    situation: UsDaySituationMap
    current_markets: tuple[UsDayCurrentMarket, ...]

    @model_validator(mode="after")
    def require_market_lineage(self) -> Self:
        leaders = {leader.symbol for theme in self.situation.themes for leader in theme.leaders}
        if {item.symbol for item in self.current_markets} != leaders or len(self.current_markets) != len(leaders):
            raise UsDayAgentServiceError("canonical_market_lineage_invalid")
        return self


class UsDaySourceReader(Protocol):
    def read(self, path: Path) -> CanonicalUsDaySource: ...


@dataclass(frozen=True, slots=True)
class LocalUsDaySourceReader:
    def read(self, path: Path) -> CanonicalUsDaySource:
        try:
            return CanonicalUsDaySource.model_validate_json(read_private_text(path))
        except (ValidationError, ValueError):
            raise UsDayAgentServiceError("canonical_source_invalid") from None


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
    task_id: str | None = None
    recommendation_id: str | None = None
    paper_status: str | None = None
    hermes_delivery_id: str | None = None
    dashboard_snapshot_id: str | None = None
    market_close_report_id: str | None = None
    challenger_version_id: str | None = None

    @model_validator(mode="after")
    def require_status_fields(self) -> Self:
        if (self.status == "blocked") != (self.reason is not None):
            raise UsDayAgentServiceError("tick_result_status_invalid")
        return self

    @classmethod
    def accepted(cls, request: UsDayAgentTickRequest, **updates: str | None) -> Self:
        return cls(
            status="accepted",
            phase=session_phase_at(request.evaluated_at),
            tick_id=tick_id_for(request),
            **updates,
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
        values = self.model_dump(mode="json", exclude_none=True, exclude={"tick_id"})
        return {key: str(value) for key, value in values.items()}


@dataclass(frozen=True, slots=True)
class UsDayExecutionAuthority:
    promotion: PromotionDecision
    eligibility: ExecutionEligibility
    lane_id: LaneId
    arm_request_id: str
    actionable_payload_sha256: str


class UsDayExecutionAuthorityReader(Protocol):
    def read(
        self,
        source: CanonicalUsDaySource,
        thesis: UsDayThesisResult,
        champion: UsDayChampion,
        evaluated_at: dt.datetime,
    ) -> UsDayExecutionAuthority: ...


class UsDayPaperSessionControl(Protocol):
    def recover_and_reconcile(self, evaluated_at: dt.datetime) -> None: ...

    def block_new_entries(self, evaluated_at: dt.datetime) -> str: ...

    def flatten(self, evaluated_at: dt.datetime) -> str: ...

    def finalize(self, evaluated_at: dt.datetime) -> str: ...


class UsDayClosePayloadReader(Protocol):
    def read(self, request: UsDayAgentTickRequest, champion: AgentVersion) -> MarketCloseReportPayload: ...


@dataclass(frozen=True, slots=True)
class UsDayLocalStores:
    outputs: Path
    task_store: DayAgentTaskStore
    thesis_store: UsDayThesisStore
    paper_store: PaperStore
    version_store: DayAgentVersionStore


@dataclass(frozen=True, slots=True)
class UsDayModelBindings:
    day_reasoner: DayAgentReasoningClient
    thesis_reasoner: Reasoner
    tools: DayAgentToolRuntime


@dataclass(frozen=True, slots=True)
class UsDayStrategyBinding:
    strategy_version: str
    strategy_lane: StrategyLaneRef
    playbooks: tuple[UsDayPlaybook, ...]
    max_steps: int = 8


@dataclass(frozen=True, slots=True)
class UsDayPaperBindings:
    operating: UsDayAgentOperatingServices
    authority_reader: UsDayExecutionAuthorityReader
    session_control: UsDayPaperSessionControl


@dataclass(frozen=True, slots=True)
class UsDayCloseBindings:
    payload_reader: UsDayClosePayloadReader
    loop_services: DayAgentLoopServices


@dataclass(frozen=True, slots=True)
class UsDayProductionConfig:
    stores: UsDayLocalStores
    models: UsDayModelBindings
    strategy: UsDayStrategyBinding
    source_reader: UsDaySourceReader
    paper: UsDayPaperBindings | None = None
    close: UsDayCloseBindings | None = None


@dataclass(frozen=True, slots=True)
class UsDayAgentServiceConfig:
    receipt_root: Path
    entry_cutoff_before_close: dt.timedelta = dt.timedelta(minutes=15)
    eod_before_close: dt.timedelta = dt.timedelta(minutes=5)

    def __post_init__(self) -> None:
        if not dt.timedelta() < self.eod_before_close < self.entry_cutoff_before_close < dt.timedelta(hours=1):
            raise UsDayAgentServiceError("session_configuration_invalid")


@dataclass(frozen=True, slots=True)
class UsDayProductionRuntime:
    config: UsDayProductionConfig
    clock: Callable[[], dt.datetime]

    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        source = self._source(request)
        map_id = self._publish_situation(source.situation)
        dashboard_id = self._dashboard(request.evaluated_at)
        return UsDayAgentTickResult.accepted(request, market_map_id=map_id, dashboard_snapshot_id=dashboard_id)

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        source = self._source(request)
        map_id = self._publish_situation(source.situation)
        version, champion = self._champion()
        task_result = run_day_agent_task(self._day_runtime(), self._task(version, source, request.evaluated_at))
        if self.config.stores.version_store.reader().champion() != version:
            raise UsDayAgentServiceError("champion_changed_during_tick")
        if task_result.state is DayAgentTaskState.BLOCKED:
            return UsDayAgentTickResult.blocked(request, task_result.task.terminal_reason or "day_agent_blocked")
        if task_result.state is not DayAgentTaskState.COMPLETED:
            return UsDayAgentTickResult.accepted(request, market_map_id=map_id, task_id=task_result.task.task_id)
        thesis_result = self._thesis(champion, source)
        if not any(
            item.thesis_id == thesis_result.thesis.thesis_id for item in self.config.stores.thesis_store.theses()
        ):
            _ = persist_and_queue_thesis(
                thesis_result.thesis,
                self.config.stores.paper_store,
                self.config.stores.thesis_store,
            )
        if thesis_result.signal is None:
            return UsDayAgentTickResult.accepted(
                request,
                market_map_id=map_id,
                task_id=task_result.task.task_id,
                recommendation_id=thesis_result.thesis.thesis_id,
                dashboard_snapshot_id=self._dashboard(request.evaluated_at),
            )
        paper = self.config.paper
        if paper is None:
            return UsDayAgentTickResult.blocked(request, "paper_bindings_missing")
        authority = paper.authority_reader.read(source, thesis_result, champion, request.evaluated_at)
        market = next(item for item in source.current_markets if item.symbol == thesis_result.signal.symbol)
        result = operate_us_day_agent(
            UsDayAgentOperatingRequest(
                admission=UsDaySignalAdmissionRequest(
                    session_id=source.situation.session_id,
                    lane_id=authority.lane_id,
                    thesis=thesis_result.thesis,
                    signal=thesis_result.signal,
                    champion=champion,
                    situation=source.situation,
                    current_market=market,
                    promotion=authority.promotion,
                    execution_eligibility=authority.eligibility,
                    evaluated_at=request.evaluated_at,
                ),
                arm_request_id=authority.arm_request_id,
                actionable_payload_sha256=authority.actionable_payload_sha256,
            ),
            paper.operating,
        )
        dashboard_id = self._dashboard(request.evaluated_at)
        return UsDayAgentTickResult.accepted(
            request,
            market_map_id=map_id,
            task_id=task_result.task.task_id,
            recommendation_id=thesis_result.thesis.thesis_id,
            paper_status=result.status.value,
            hermes_delivery_id=result.outcome_delivery_id,
            dashboard_snapshot_id=dashboard_id,
        )

    def cutoff(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        paper = self._paper()
        return UsDayAgentTickResult.accepted(
            request,
            paper_status=paper.session_control.block_new_entries(request.evaluated_at),
            dashboard_snapshot_id=self._dashboard(request.evaluated_at),
        )

    def eod(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        paper = self._paper()
        return UsDayAgentTickResult.accepted(
            request,
            paper_status=paper.session_control.flatten(request.evaluated_at),
            dashboard_snapshot_id=self._dashboard(request.evaluated_at),
        )

    def post_close(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        paper = self._paper()
        finalized = paper.session_control.finalize(request.evaluated_at)
        close = self.config.close
        if close is None:
            return UsDayAgentTickResult.blocked(request, "close_bindings_missing")
        version, _ = self._champion()
        report = seal_market_close_report(close.payload_reader.read(request, version))
        _ = publish_market_close_report(self.config.stores.outputs / "us_day" / "close_reports", report)
        proposal = run_loop_engineer(report, version, close.loop_services)
        return UsDayAgentTickResult.accepted(
            request,
            paper_status=finalized,
            market_close_report_id=report.report_id,
            challenger_version_id=proposal.version_id,
            dashboard_snapshot_id=self._dashboard(request.evaluated_at),
        )

    def recover(self, request: UsDayAgentTickRequest) -> None:
        self._paper().session_control.recover_and_reconcile(request.evaluated_at)

    def _source(self, request: UsDayAgentTickRequest) -> CanonicalUsDaySource:
        source = self.config.source_reader.read(request.situation_path)
        situation = source.situation
        if (
            situation.session_date != request.evaluated_at.astimezone(NEW_YORK).date()
            or situation.evaluated_at > request.evaluated_at
            or request.evaluated_at - situation.evaluated_at > dt.timedelta(minutes=15)
        ):
            raise UsDayAgentServiceError("situation_stale")
        return source

    def _champion(self) -> tuple[AgentVersion, UsDayChampion]:
        version = self.config.stores.version_store.reader().champion()
        strategy = self.config.strategy
        if version is None or version.deployment_state is not AgentDeploymentState.CHAMPION:
            raise UsDayAgentServiceError("champion_missing")
        return version, UsDayChampion(
            version_id=version.version_id,
            strategy_version=strategy.strategy_version,
            strategy_lane=strategy.strategy_lane,
            deployed=True,
            playbooks=strategy.playbooks,
        )

    def _task(
        self,
        version: AgentVersion,
        source: CanonicalUsDaySource,
        evaluated_at: dt.datetime,
    ) -> DayAgentResearchTask:
        existing = self.config.stores.task_store.reader().task(version.task_id)
        if existing is not None:
            return existing
        refs = tuple(sorted(item.canonical_id for item in source.situation.evidence_refs))[:64]
        return DayAgentResearchTask(
            task_id=version.task_id,
            objective="Assess the current XNYS Day setup using canonical completed-bar evidence.",
            question="Does the current session support a Champion trade thesis?",
            current_hypothesis="The current theme leader may support a bounded Day thesis.",
            falsification_conditions=("current_session_evidence_refuted",),
            open_questions=("is_champion_setup_eligible",),
            resume_condition=None,
            state=DayAgentTaskState.OPEN,
            evidence_refs=refs,
            budget=DayAgentBudget(remaining_model_calls=8, remaining_tool_calls=16, remaining_runtime_seconds=180),
            created_at=evaluated_at,
            updated_at=evaluated_at,
        )

    def _day_runtime(self) -> DayAgentRuntime:
        return DayAgentRuntime(
            store=self.config.stores.task_store,
            reasoner=self.config.models.day_reasoner,
            tools=self.config.models.tools,
            max_steps=self.config.strategy.max_steps,
            clock=self.clock,
        )

    def _thesis(self, champion: UsDayChampion, source: CanonicalUsDaySource) -> UsDayThesisResult:
        situation_id = situation_id_for(source.situation)
        prior = next(
            (
                item
                for item in self.config.stores.thesis_store.theses()
                if item.situation_id == situation_id and item.agent_version_id == champion.version_id
            ),
            None,
        )
        if prior is not None:
            return UsDayThesisResult(thesis=prior, signal=None)
        try:
            return generate_trade_thesis(
                self.config.models.thesis_reasoner,
                champion,
                source.situation,
                source.current_markets,
            )
        except InvalidUsDayThesisError:
            raise UsDayAgentServiceError("thesis_invalid") from None

    def _publish_situation(self, situation: UsDaySituationMap) -> str:
        map_id = situation_id_for(situation)
        _ = publish_private_immutable_text(
            self.config.stores.outputs / "us_day" / "situations" / f"{map_id}.json",
            situation.model_dump_json(),
        )
        return map_id

    def _dashboard(self, evaluated_at: dt.datetime) -> str:
        snapshot = collect_dashboard_snapshot_v2(
            self.config.stores.outputs,
            now=evaluated_at,
            day_version_reader=self.config.stores.version_store.reader(),
        )
        _ = publish_private_immutable_text(
            self.config.stores.outputs / "us_day" / "dashboard" / f"{snapshot.snapshot_id}.json",
            snapshot.model_dump_json(),
        )
        return str(snapshot.snapshot_id)

    def _paper(self) -> UsDayPaperBindings:
        if self.config.paper is None:
            raise UsDayAgentServiceError("paper_bindings_missing")
        return self.config.paper


class UsDaySessionRuntime(Protocol):
    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def cutoff(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def eod(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def post_close(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult: ...

    def recover(self, request: UsDayAgentTickRequest) -> None: ...


@dataclass(frozen=True, slots=True)
class UsDayAgentService:
    config: UsDayAgentServiceConfig
    runtime: UsDaySessionRuntime
    clock: Callable[[], dt.datetime]

    def tick_from_source(self, situation_path: Path) -> UsDayAgentTickResult:
        payload = situation_path.read_bytes()
        return self.tick(
            UsDayAgentTickRequest(
                situation_path=situation_path,
                evaluated_at=self.clock(),
                source_sha256=hashlib.sha256(payload).hexdigest(),
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
        try:
            match phase:
                case UsDaySessionPhase.PREMARKET:
                    result = self.runtime.premarket(request)
                case UsDaySessionPhase.REGULAR:
                    self.runtime.recover(request)
                    result = self.runtime.regular(request)
                case UsDaySessionPhase.ENTRY_CUTOFF:
                    self.runtime.recover(request)
                    result = self.runtime.cutoff(request)
                case UsDaySessionPhase.EOD:
                    self.runtime.recover(request)
                    result = self.runtime.eod(request)
                case UsDaySessionPhase.POST_CLOSE:
                    self.runtime.recover(request)
                    result = self.runtime.post_close(request)
                case UsDaySessionPhase.CLOSED:
                    result = UsDayAgentTickResult.blocked(request, "xnys_session_closed")
                case unreachable:
                    assert_never(unreachable)
        except UsDayAgentServiceError as error:
            result = UsDayAgentTickResult.blocked(request, error.reason)
        if result.phase is not phase or result.tick_id != tick_id_for(request):
            raise UsDayAgentServiceError("vertical_result_identity_invalid")
        return _publish_receipt(self.config.receipt_root, result)


def build_us_day_agent_service(
    production: UsDayProductionConfig,
    session: UsDayAgentServiceConfig,
    clock: Callable[[], dt.datetime],
) -> UsDayAgentService:
    runtime = UsDayProductionRuntime(production, clock)
    return UsDayAgentService(session, runtime, clock)


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


def _read_receipt(root: Path, request: UsDayAgentTickRequest) -> UsDayAgentTickResult | None:
    path = root.expanduser().absolute() / f"{tick_id_for(request)}.json"
    if not path.exists():
        return None
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise UsDayAgentServiceError("tick_receipt_metadata_invalid") from None
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
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise UsDayAgentServiceError("tick_receipt_metadata_invalid") from None
        existing = UsDayAgentTickResult.model_validate_json(path.read_text(encoding="utf-8"))
        if existing != result:
            raise UsDayAgentServiceError("tick_receipt_conflict") from None
        return existing
    except OSError:
        raise UsDayAgentServiceError("tick_receipt_write_failed") from None
