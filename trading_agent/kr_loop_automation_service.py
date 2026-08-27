from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_autonomous_operator_paths import kr_autonomous_operator_paths
from trading_agent.kr_loop_automation_config import KrLoopAutomationConfig
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot, KrLoopCandidateState, KrLoopReleaseAction
from trading_agent.kr_loop_engineer_mutation import (
    GrokKrLoopMutationWorker,
    KrLoopMutationExecutor,
    KrLoopMutationWorker,
)
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_engineer_sync import find_kr_loop_bundle, sync_kr_loop_bundles
from trading_agent.kr_loop_health_monitor import monitor_active_release
from trading_agent.kr_loop_release_artifacts import KrLoopReleaseArtifactStore
from trading_agent.kr_loop_release_reconciler import LaunchctlRunner, reconcile_active_release
from trading_agent.kr_loop_shadow_runtime import ShadowRunner, run_shadow_session
from trading_agent.repository_current_main import current_main_commit
from trading_agent.research_agent_service_config import (
    load_research_agent_service_config,
)

CommitReader = Callable[[Path], str]
_KST = dt.timezone(dt.timedelta(hours=9))
_POST_CLOSE = dt.time(15, 40)
_HEALTH_GRACE = dt.timedelta(minutes=3)


class InvalidKrLoopAutomationServiceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop automation service failed"


@dataclass(frozen=True, slots=True)
class KrLoopAutomationTickResult:
    status: Literal["session_unavailable", "idle", "mutated", "shadowed", "promoted", "rolled_back", "evidence_pending"]
    session_date: dt.date | None
    mutated_candidate_id: str | None
    shadow_candidate_id: str | None
    release_action: str | None
    paper_only: Literal[True] = True
    trading_authority: Literal[False] = False


def completed_kr_session(config: KrLoopAutomationConfig, now: dt.datetime) -> dt.date | None:
    research = load_research_agent_service_config(config.research_agent_config)
    calendar = research.source_paths.kr_calendar_store
    local = now.astimezone(_KST)
    if calendar is None or local.time() < _POST_CLOSE:
        return None
    snapshots = KisKrSessionCalendarStore(calendar).snapshots()
    current = tuple(snapshot for snapshot in snapshots if snapshot.payload.base_date == local.date())
    if len(current) != 1:
        return None
    days = tuple(day for day in current[0].payload.days if day.session_date == local.date())
    if len(days) != 1 or not days[0].business_day or not days[0].trading_day or not days[0].open_day:
        return None
    return local.date()


def run_automation_tick(
    config: KrLoopAutomationConfig,
    now: dt.datetime,
    *,
    commit_reader: CommitReader = current_main_commit,
    mutation_worker: KrLoopMutationWorker | None = None,
    shadow_runner: ShadowRunner | None = None,
    launchctl_runner: LaunchctlRunner | None = None,
) -> KrLoopAutomationTickResult:
    session_date = completed_kr_session(config, now)
    if session_date is None:
        return _result("session_unavailable", None, None, None, None)
    research = load_research_agent_service_config(config.research_agent_config)
    paths = kr_autonomous_operator_paths(research)
    if paths is None:
        raise InvalidKrLoopAutomationServiceError
    store = KrLoopEngineerStore(paths.loop_database)
    artifacts = KrLoopReleaseArtifactStore(paths.loop_artifact_root)
    worker = GrokKrLoopMutationWorker(str(config.grok_binary)) if mutation_worker is None else mutation_worker
    controller = KrLoopEngineerController(
        store,
        KrLoopMutationExecutor(
            repository=config.repository,
            task_root=paths.loop_task_root,
            artifact_root=paths.loop_artifact_root,
            worker=worker,
        ),
    )
    mutated: str | None = None
    shadowed: str | None = None
    releases = store.releases()
    deployment_changed = False
    if releases:
        deployment = reconcile_active_release(
            store=store,
            artifacts=artifacts,
            repository=config.repository,
            active_path=config.active_release,
            now=now,
            runner=launchctl_runner,
        )
        deployment_changed = deployment.changed
    if not releases or releases[-1].action is KrLoopReleaseAction.ROLLBACK:
        base_commit = commit_reader(config.repository)
        _ = sync_kr_loop_bundles(paths, base_commit=base_commit, now=now)
        pending = _latest_in_state(store, KrLoopCandidateState.DETECTED)
        if pending is not None:
            bundle = find_kr_loop_bundle(paths, pending.bundle_id)
            if bundle is None:
                raise InvalidKrLoopAutomationServiceError
            mutated_state = controller.mutate(bundle, now=now)
            mutated = mutated_state.candidate_id
    candidate = _shadow_candidate(store, session_date)
    status: Literal["idle", "mutated", "shadowed", "promoted", "rolled_back", "evidence_pending"] = (
        "mutated" if mutated is not None else "idle"
    )
    if candidate is not None:
        bundle = find_kr_loop_bundle(paths, candidate.bundle_id)
        if bundle is None:
            raise InvalidKrLoopAutomationServiceError
        artifact = artifacts.verified(candidate.candidate_id)
        shadow = run_shadow_session(
            base_config=research,
            champion_root=artifact.baseline_root,
            challenger_root=artifact.candidate_root,
            shadow_root=config.shadow_root,
            candidate_id=candidate.candidate_id,
            failure_code=bundle.failure_code,
            session_date=session_date,
            observed_at=now,
            runner=shadow_runner,
        )
        shadowed = candidate.candidate_id
        if shadow.receipt is None:
            status = "evidence_pending"
        else:
            state = controller.record_shadow(candidate.candidate_id, shadow.receipt)
            status = "promoted" if state.state is KrLoopCandidateState.PROMOTED else "shadowed"
    releases = store.releases()
    if releases:
        deployment = reconcile_active_release(
            store=store,
            artifacts=artifacts,
            repository=config.repository,
            active_path=config.active_release,
            now=now,
            runner=launchctl_runner,
        )
        deployment_changed = deployment_changed or deployment.changed
        active = store.releases()[-1]
        latest = store.latest(active.candidate_id)
        if (
            active.action is KrLoopReleaseAction.PROMOTE
            and latest is not None
            and latest.state is KrLoopCandidateState.PROMOTED
            and not deployment_changed
            and now >= active.recorded_at + _HEALTH_GRACE
        ):
            health = monitor_active_release(
                controller=controller,
                config=research,
                artifacts=artifacts,
                repository=config.repository,
                active_path=config.active_release,
                observed_at=now,
                runner=launchctl_runner,
            )
            if health.candidate.state is KrLoopCandidateState.ROLLED_BACK:
                status = "rolled_back"
    action = None if not store.releases() else store.releases()[-1].action.value
    return _result(status, session_date, mutated, shadowed, action)


def _latest_in_state(store: KrLoopEngineerStore, state: KrLoopCandidateState) -> KrLoopCandidateSnapshot | None:
    latest = {item.candidate_id: item for item in store.snapshots()}
    return next((item for item in latest.values() if item.state is state), None)


def _shadow_candidate(store: KrLoopEngineerStore, session_date: dt.date) -> KrLoopCandidateSnapshot | None:
    candidates = tuple(
        item
        for item in {value.candidate_id: value for value in store.snapshots()}.values()
        if item.state is KrLoopCandidateState.SHADOWING
        and session_date > item.created_at.date()
        and session_date not in {receipt.session_date for receipt in item.shadow_receipts}
    )
    return min(candidates, key=lambda item: (item.created_at, item.candidate_id), default=None)


def _result(status, session_date, mutated, shadowed, action) -> KrLoopAutomationTickResult:
    return KrLoopAutomationTickResult(
        status=status,
        session_date=session_date,
        mutated_candidate_id=mutated,
        shadow_candidate_id=shadowed,
        release_action=action,
    )


__all__ = (
    "CommitReader",
    "InvalidKrLoopAutomationServiceError",
    "KrLoopAutomationTickResult",
    "completed_kr_session",
    "run_automation_tick",
)
