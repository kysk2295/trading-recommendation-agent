from __future__ import annotations

import datetime as dt
import hashlib
import time
from collections.abc import Callable
from pathlib import Path

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.future_session_kr_activation_verifier import (
    verify_kr_supervisor_preflight,
    verify_kr_supervisor_restart_preflight,
)
from trading_agent.future_session_kr_manifest import KrFutureSessionPreparationManifest
from trading_agent.future_session_kr_supervisor_commands import (
    KrSupervisorPaths,
    cycle_opportunities,
    kr_supervisor_commands,
    kr_supervisor_opportunity_commands,
    require_read_only_commands,
)
from trading_agent.future_session_kr_supervisor_incident import (
    KrSupervisorIncidentRequest,
    project_kr_supervisor_incident,
)
from trading_agent.future_session_kr_supervisor_models import (
    InvalidKrFutureSessionSupervisorError,
    KrFutureSessionSupervisorState,
    KrSupervisorCycleOutcome,
    KrSupervisorPhase,
    KrSupervisorResult,
    kr_supervisor_state_path,
)
from trading_agent.future_session_kr_supervisor_schedule import (
    KrSupervisorPhaseWindow,
    await_kr_supervisor_phase_window,
    kr_session_close_epoch,
)
from trading_agent.future_session_kr_supervisor_store import (
    load_kr_supervisor_state,
    persist_kr_supervisor_state,
)
from trading_agent.future_session_plan_models import FutureSessionPlanRequest
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_research_chain_rollover import (
    kr_theme_research_rollover_bundle_sha256,
    load_kr_theme_research_rollover_bundle,
)

Clock = Callable[[], dt.datetime]
Sleeper = Callable[[float], None]
KrSupervisorCommandRunner = Callable[[tuple[str, ...]], int]


def run_kr_future_session_supervisor(
    manifest_path: Path,
    *,
    runner: KrSupervisorCommandRunner,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    sleeper: Sleeper = time.sleep,
) -> KrFutureSessionSupervisorState:
    manifest_payload = manifest_path.read_bytes()
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    state_path = kr_supervisor_state_path(manifest_path)
    existing = load_kr_supervisor_state(state_path, manifest_hash)
    if existing is None:
        verify_kr_supervisor_preflight(manifest_path)
    else:
        verify_kr_supervisor_restart_preflight(manifest_path)
    manifest = KrFutureSessionPreparationManifest.model_validate_json(manifest_payload)
    request = FutureSessionPlanRequest.model_validate_json(manifest.request_file.read_bytes())
    if request.kr_rollover_bundle is None or request.delivery_database is None:
        raise InvalidKrFutureSessionSupervisorError
    delivery_database = request.delivery_database
    bundle = load_kr_theme_research_rollover_bundle(request.kr_rollover_bundle)
    policy_hash = hashlib.sha256(canonical_experiment_ledger_json(bundle.opportunity_policy).encode()).hexdigest()
    if (
        kr_theme_research_rollover_bundle_sha256(bundle) != manifest.kr_rollover_bundle_sha256
        or policy_hash != manifest.kr_policy_sha256
    ):
        raise InvalidKrFutureSessionSupervisorError
    if existing is not None and existing.result is not KrSupervisorResult.WAITING:
        return existing
    paths = KrSupervisorPaths.from_root(manifest_path.parent)
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.root.chmod(0o700)
    commands = kr_supervisor_commands(request, bundle, paths, dt.date.fromisoformat(manifest.target_session))
    require_read_only_commands(commands)
    state = existing or KrFutureSessionSupervisorState(manifest_sha256=manifest_hash)
    target = dt.date.fromisoformat(manifest.target_session)
    phases = manifest.internal_phase_epochs

    def phase_window(start: int, deadline: int) -> bool:
        return await_kr_supervisor_phase_window(KrSupervisorPhaseWindow(start, deadline), clock, sleeper)

    def incident(phase: KrSupervisorPhase) -> KrFutureSessionSupervisorState:
        project_kr_supervisor_incident(
            KrSupervisorIncidentRequest(
                manifest_hash,
                target,
                phase,
                delivery_database,
                bundle.opportunity_version.strategy_version,
                clock(),
            )
        )
        return _finish(state_path, state, KrSupervisorResult.INCIDENT)

    if not phase_window(phases[0], phases[1]):
        return incident(KrSupervisorPhase.CALENDAR)
    now = clock()
    if now.astimezone(dt.timezone(dt.timedelta(hours=9))).date() != target:
        return incident(KrSupervisorPhase.CALENDAR)
    state = _run_phase(state_path, state, KrSupervisorPhase.CALENDAR, commands["calendar"], runner)
    if KrSupervisorPhase.CALENDAR not in state.completed_phases or not _current_open_day(paths.calendar, target):
        return incident(KrSupervisorPhase.CALENDAR)
    for phase, key in (
        (KrSupervisorPhase.COMPOSITE, "composite"),
        (KrSupervisorPhase.REGISTER, "register"),
    ):
        if not phase_window(phases[1], phases[2]):
            return incident(phase)
        state = _run_phase(state_path, state, phase, commands[key], runner)
        if phase not in state.completed_phases:
            return incident(phase)
    if not phase_window(phases[2], phases[3]):
        return incident(KrSupervisorPhase.START)
    state = _run_phase(state_path, state, KrSupervisorPhase.START, commands["start"], runner)
    if KrSupervisorPhase.START not in state.completed_phases:
        return incident(KrSupervisorPhase.START)
    close_epoch = kr_session_close_epoch(target)
    if not phase_window(phases[3], close_epoch):
        return incident(KrSupervisorPhase.CYCLE)
    cycle_outcome, state = _run_cycle(state_path, state, commands["cycle"], runner)
    if cycle_outcome is KrSupervisorCycleOutcome.BLOCKED:
        return incident(KrSupervisorPhase.CYCLE)
    cycle_id = commands["cycle"][commands["cycle"].index("--collection-cycle-id") + 1]
    opportunities = (
        cycle_opportunities(paths.outbox, cycle_id) if cycle_outcome is KrSupervisorCycleOutcome.READY else ()
    )
    if len(opportunities) > 1:
        return incident(KrSupervisorPhase.CYCLE)
    if not opportunities:
        if not phase_window(phases[4], phases[5]):
            return incident(KrSupervisorPhase.POST)
        state = _run_phase(state_path, state, KrSupervisorPhase.POST, commands["post"], runner)
        result: KrSupervisorResult = (
            KrSupervisorResult.TERMINAL_NO_RECOMMENDATION
            if KrSupervisorPhase.POST in state.completed_phases
            else KrSupervisorResult.INCIDENT
        )
        return (
            incident(KrSupervisorPhase.POST)
            if result is KrSupervisorResult.INCIDENT
            else _finish(state_path, state, result)
        )
    opportunity = opportunities[0]
    opportunity_commands = kr_supervisor_opportunity_commands(
        request,
        bundle,
        paths,
        target,
        opportunity.opportunity_id,
    )
    require_read_only_commands(opportunity_commands)
    if not phase_window(phases[3], close_epoch):
        return incident(KrSupervisorPhase.ONBOARD)
    state = persist_kr_supervisor_state(
        state_path,
        state.model_copy(update={"opportunity_id": opportunity.opportunity_id}),
    )
    state = _run_phase(
        state_path,
        state,
        KrSupervisorPhase.ONBOARD,
        opportunity_commands["onboard"],
        runner,
    )
    if KrSupervisorPhase.ONBOARD not in state.completed_phases:
        return incident(KrSupervisorPhase.ONBOARD)
    tick_schedule = (
        (KrSupervisorPhase.TICK_OPEN, phases[3], close_epoch),
        (KrSupervisorPhase.TICK_CLOSE, close_epoch, phases[4]),
        (KrSupervisorPhase.TICK_POST, phases[4], phases[5]),
    )
    for phase, epoch, deadline in tick_schedule:
        if not phase_window(epoch, deadline):
            return incident(phase)
        state = _run_phase(state_path, state, phase, opportunity_commands["tick"], runner)
        if phase not in state.completed_phases:
            return incident(phase)
    if not phase_window(phases[5], phases[5] + 900):
        return incident(KrSupervisorPhase.VERIFY)
    state = _run_phase(
        state_path,
        state,
        KrSupervisorPhase.VERIFY,
        opportunity_commands["verify"],
        runner,
    )
    result = (
        KrSupervisorResult.TERMINAL_VERIFIED
        if KrSupervisorPhase.VERIFY in state.completed_phases
        else KrSupervisorResult.INCIDENT
    )
    return (
        incident(KrSupervisorPhase.VERIFY)
        if result is KrSupervisorResult.INCIDENT
        else _finish(state_path, state, result)
    )


def _run_cycle(
    path: Path,
    state: KrFutureSessionSupervisorState,
    command: tuple[str, ...],
    runner: KrSupervisorCommandRunner,
) -> tuple[KrSupervisorCycleOutcome, KrFutureSessionSupervisorState]:
    if KrSupervisorPhase.CYCLE in state.completed_phases:
        if state.cycle_outcome is None:
            raise InvalidKrFutureSessionSupervisorError
        return state.cycle_outcome, state
    exit_code = runner(command)
    outcome = KrSupervisorCycleOutcome.READY if exit_code == 0 else KrSupervisorCycleOutcome.BLOCKED
    updated = state.model_copy(
        update={
            "completed_phases": (*state.completed_phases, KrSupervisorPhase.CYCLE),
            "cycle_outcome": outcome,
        }
    )
    return outcome, persist_kr_supervisor_state(path, updated)


def _run_phase(
    path: Path,
    state: KrFutureSessionSupervisorState,
    phase: KrSupervisorPhase,
    command: tuple[str, ...],
    runner: KrSupervisorCommandRunner,
) -> KrFutureSessionSupervisorState:
    if phase in state.completed_phases:
        return state
    if runner(command) != 0:
        return state
    return persist_kr_supervisor_state(
        path,
        state.model_copy(update={"completed_phases": (*state.completed_phases, phase)}),
    )


def _current_open_day(path: Path, target: dt.date) -> bool:
    snapshots = KisKrSessionCalendarStore(path).snapshots()
    matches = tuple(snapshot for snapshot in snapshots if snapshot.payload.base_date == target)
    return len(matches) == 1 and any(
        day.session_date == target and day.open_day and day.business_day and day.trading_day
        for day in matches[0].payload.days
    )


def _finish(
    path: Path,
    state: KrFutureSessionSupervisorState,
    result: KrSupervisorResult,
) -> KrFutureSessionSupervisorState:
    return persist_kr_supervisor_state(path, state.model_copy(update={"result": result}))


__all__ = ("KrSupervisorCommandRunner", "run_kr_future_session_supervisor")
