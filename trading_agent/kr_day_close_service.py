from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never, override

from trading_agent.day_learning_policy import ExplorationPolicyAction
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_day_close_service_config import (
    InvalidKrDayCloseServiceConfigError,
    KrDayCloseServiceConfig,
    kr_day_close_service_config_sha256,
    require_kr_day_close_service_authority,
)
from trading_agent.kr_day_close_service_request import (
    InvalidKrDayCloseRequestSourceError,
    KrDayCloseNotReadyError,
    build_kr_day_close_request,
)
from trading_agent.kr_day_close_service_state import (
    CloseStage,
    KrDayCloseCompletionReceipt,
    KrDayCloseServiceHealth,
    KrDayCloseServiceResult,
    publish_kr_day_close_completion,
    write_kr_day_close_health,
)
from trading_agent.kr_day_decision_delivery import (
    KrDayDecisionDeliveryBatch,
    project_kr_day_decision_delivery,
)
from trading_agent.kr_day_learning_policy import (
    KrDayLearningPolicyPublication,
    publish_kr_day_learning_policy,
)
from trading_agent.kr_day_loop_engineer import (
    KrDayLoopAuthorityPaths,
    run_configured_kr_day_loop_engineer,
)
from trading_agent.kr_day_market_close_report import (
    KrDayMarketClosePublication,
    publish_kr_day_market_close_report,
)

type ServiceClock = Callable[[], dt.datetime]
type StageObserver = Callable[[CloseStage], None]
type CloseLoopEngineer = Callable[
    [KrDayMarketClosePublication, KrDayLearningPolicyPublication],
    Literal[0, 1],
]


class InvalidKrDayCloseServiceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR day-close service failed"


@dataclass(frozen=True, slots=True)
class KrDayCloseRuntime:
    clock: ServiceClock
    stage_observer: StageObserver
    loop_engineer: CloseLoopEngineer | None = None


def run_kr_day_close_service(
    config: KrDayCloseServiceConfig,
    runtime: KrDayCloseRuntime | None = None,
) -> KrDayCloseServiceResult:
    active = _runtime() if runtime is None else runtime
    observed_at = active.clock()
    config_sha = kr_day_close_service_config_sha256(config)
    stage: CloseStage = "binding"
    session_date: dt.date | None = None
    try:
        with _service_lease(config.state_root):
            require_kr_day_close_service_authority(config)
            stage = "request"
            try:
                request = build_kr_day_close_request(config, observed_at)
            except KrDayCloseNotReadyError as no_action:
                result = _result("no_action", no_action.reason, stage, no_action.session_date)
            else:
                session_date = request.session_date
                stage = "report"
                publication = publish_kr_day_market_close_report(config.report_root, request)
                active.stage_observer(stage)
                stage = "policy"
                policy = publish_kr_day_learning_policy(
                    config.report_root,
                    config.policy_root,
                    publication.report,
                    request.calendar_snapshot,
                    ExplorationPolicyAction.KEEP,
                )
                active.stage_observer(stage)
                stage = "loop"
                challenger_count = (
                    run_configured_kr_day_loop_engineer(
                        publication.report,
                        publication.metrics,
                        policy.policy,
                        KrDayLoopAuthorityPaths(config.state_root, config.experiment_ledger),
                    ).challenger_count
                    if active.loop_engineer is None
                    else active.loop_engineer(publication, policy)
                )
                active.stage_observer(stage)
                stage = "summary"
                with HermesDeliveryStore(config.hermes_delivery_database).writer() as writer:
                    summary = project_kr_day_decision_delivery(
                        KrDayDecisionDeliveryBatch(
                            decision_events=(),
                            shadow_events=(),
                            close_reports=(publication.report,),
                            challenger_count=challenger_count,
                        ),
                        writer,
                    )
                active.stage_observer(stage)
                stage = "completion"
                _ = publish_kr_day_close_completion(
                    config.completion_root,
                    KrDayCloseCompletionReceipt(
                        session_date=request.session_date,
                        config_sha256=config_sha,
                        calendar_snapshot_id=request.calendar_snapshot.snapshot_id,
                        report_id=publication.report.report_id,
                        metrics_id=publication.metrics.metrics_id,
                        policy_id=policy.policy.policy_id,
                        challenger_count=challenger_count,
                        summary_source_event_id=f"kr-day:summary:{publication.report.report_id}",
                        completed_at=request.finalized_at,
                    ),
                )
                active.stage_observer(stage)
                result = KrDayCloseServiceResult(
                    status="completed",
                    reason="session_finalized",
                    stage=stage,
                    session_date=request.session_date,
                    complete=True,
                    report_id=publication.report.report_id,
                    metrics_id=publication.metrics.metrics_id,
                    policy_id=policy.policy.policy_id,
                    challenger_count=challenger_count,
                    summary_inserted=summary.inserted,
                )
    except (
        InvalidKrDayCloseRequestSourceError,
        InvalidKrDayCloseServiceConfigError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as error:
        result = _result("blocked", _failure_reason(stage, error), stage, session_date)
    health = KrDayCloseServiceHealth(
        **result.model_dump(mode="python"),
        config_sha256=config_sha,
        observed_at=observed_at,
    )
    _ = write_kr_day_close_health(config.health_root, health)
    return result


@contextmanager
def _service_lease(state_root: Path) -> Iterator[None]:
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)
    descriptor = os.open(
        state_root / ".kr-day-close.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _runtime() -> KrDayCloseRuntime:
    return KrDayCloseRuntime(
        clock=lambda: dt.datetime.now(tz=dt.UTC),
        stage_observer=lambda _stage: None,
    )


def _result(
    status: str,
    reason: str,
    stage: CloseStage,
    session_date: dt.date | None,
) -> KrDayCloseServiceResult:
    return KrDayCloseServiceResult.model_validate(
        {
            "status": status,
            "reason": reason,
            "stage": stage,
            "session_date": session_date,
            "complete": False,
            "report_id": None,
            "metrics_id": None,
            "policy_id": None,
            "challenger_count": 0,
            "summary_inserted": 0,
            "mutation_count": 0,
            "provider_read_only": True,
        }
    )


def _failure_reason(
    stage: CloseStage,
    error: OSError | RuntimeError | sqlite3.Error | TypeError | ValueError,
) -> str:
    match error:
        case InvalidKrDayCloseServiceConfigError():
            detail = "binding_invalid"
        case InvalidKrDayCloseRequestSourceError():
            detail = "source_invalid"
        case OSError() | RuntimeError() | sqlite3.Error() | TypeError() | ValueError():
            detail = "publication_failed"
        case unreachable:
            assert_never(unreachable)
    return f"{stage}_{detail}"


__all__ = (
    "InvalidKrDayCloseServiceError",
    "KrDayCloseRuntime",
    "run_kr_day_close_service",
)
