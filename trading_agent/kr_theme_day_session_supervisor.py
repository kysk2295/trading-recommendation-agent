from __future__ import annotations

import datetime as dt
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, override
from zoneinfo import ZoneInfo

from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEvent,
    KrThemeDaySessionPhaseEventRequest,
    KrThemeDaySessionPhaseStatus,
    build_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_commands import kr_theme_day_session_child_command
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest

CommandRunner = Callable[[tuple[str, ...]], int]
Clock = Callable[[], dt.datetime]
KST: Final = ZoneInfo("Asia/Seoul")


class InvalidKrThemeDaySessionSupervisorError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day session supervisor input is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeDaySessionTickResult:
    completed_phases: tuple[KrThemeDaySessionPhase, ...]
    blocked_phase: KrThemeDaySessionPhase | None


def run_kr_theme_day_session_tick(
    manifest: KrThemeDaySessionManifest,
    observed_at: dt.datetime,
    *,
    runner: CommandRunner = lambda command: subprocess.run(command, check=False).returncode,
    clock: Clock | None = None,
) -> KrThemeDaySessionTickResult:
    local = _local_session_time(manifest, observed_at)
    current_time = (lambda: observed_at) if clock is None else clock
    store = KrThemeDaySessionAuditStore(manifest.paths.audit_store)
    history = store.events(manifest.session_id)
    desired = _desired_phases(local, history)
    completed: list[KrThemeDaySessionPhase] = []
    for index, phase in enumerate(desired):
        phase_observed_at = observed_at if index == 0 else current_time()
        phase_local = _local_session_time(manifest, phase_observed_at)
        _require_same_cycle(local, phase_local, phase)
        cycle_key = _cycle_key(phase, local)
        if _completed(history, phase, cycle_key):
            continue
        try:
            exit_code = runner(kr_theme_day_session_child_command(manifest, phase, phase_observed_at))
        except OSError:
            exit_code = 1
        status = KrThemeDaySessionPhaseStatus.COMPLETED if exit_code == 0 else KrThemeDaySessionPhaseStatus.BLOCKED
        previous = None if not history else history[-1].event_id
        request = KrThemeDaySessionPhaseEventRequest(
            manifest.session_id,
            phase,
            cycle_key,
            phase_observed_at,
            status,
            exit_code,
        )
        event = build_kr_theme_day_session_phase_event(request, len(history) + 1, previous)
        if not store.append(event):
            raise InvalidKrThemeDaySessionSupervisorError
        history += (event,)
        if exit_code != 0:
            return KrThemeDaySessionTickResult(tuple(completed), phase)
        completed.append(phase)
    return KrThemeDaySessionTickResult(tuple(completed), None)


def _desired_phases(
    local: dt.datetime,
    history: tuple[KrThemeDaySessionPhaseEvent, ...],
) -> tuple[KrThemeDaySessionPhase, ...]:
    time = local.time()
    if time < dt.time(9):
        return (KrThemeDaySessionPhase.REGISTER,)
    if time < dt.time(9, 1):
        return (KrThemeDaySessionPhase.REGISTER, KrThemeDaySessionPhase.START)
    if time < dt.time(15, 30):
        return (
            KrThemeDaySessionPhase.REGISTER,
            KrThemeDaySessionPhase.START,
            KrThemeDaySessionPhase.INTRADAY_COLLECT,
            KrThemeDaySessionPhase.INTRADAY_ENTRY,
            KrThemeDaySessionPhase.INTRADAY_EXIT,
        )
    if time < dt.time(15, 31):
        return (KrThemeDaySessionPhase.EOD_COLLECT, KrThemeDaySessionPhase.EOD_EXIT)
    required = (KrThemeDaySessionPhase.REGISTER, KrThemeDaySessionPhase.START, KrThemeDaySessionPhase.EOD_COLLECT)
    if any(not _completed_any_cycle(history, phase) for phase in required):
        raise InvalidKrThemeDaySessionSupervisorError
    return (KrThemeDaySessionPhase.EOD_EXIT, KrThemeDaySessionPhase.POST_SESSION)


def _cycle_key(phase: KrThemeDaySessionPhase, local: dt.datetime) -> str:
    match phase:
        case (
            KrThemeDaySessionPhase.INTRADAY_COLLECT
            | KrThemeDaySessionPhase.INTRADAY_ENTRY
            | KrThemeDaySessionPhase.INTRADAY_EXIT
        ):
            return local.isoformat(timespec="minutes")
        case KrThemeDaySessionPhase.REGISTER | KrThemeDaySessionPhase.START:
            return "session"
        case KrThemeDaySessionPhase.EOD_COLLECT | KrThemeDaySessionPhase.EOD_EXIT:
            return "eod"
        case KrThemeDaySessionPhase.POST_SESSION:
            return "post_session"


def _completed(
    history: tuple[KrThemeDaySessionPhaseEvent, ...],
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
) -> bool:
    return any(
        event.phase is phase and event.cycle_key == cycle_key and event.status is KrThemeDaySessionPhaseStatus.COMPLETED
        for event in history
    )


def _completed_any_cycle(
    history: tuple[KrThemeDaySessionPhaseEvent, ...],
    phase: KrThemeDaySessionPhase,
) -> bool:
    return any(event.phase is phase and event.status is KrThemeDaySessionPhaseStatus.COMPLETED for event in history)


def _local_session_time(manifest: KrThemeDaySessionManifest, observed_at: dt.datetime) -> dt.datetime:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise InvalidKrThemeDaySessionSupervisorError
    local = observed_at.astimezone(KST)
    if local.date() != manifest.session_date:
        raise InvalidKrThemeDaySessionSupervisorError
    return local


def _require_same_cycle(
    initial: dt.datetime,
    current: dt.datetime,
    phase: KrThemeDaySessionPhase,
) -> None:
    if phase in {
        KrThemeDaySessionPhase.INTRADAY_COLLECT,
        KrThemeDaySessionPhase.INTRADAY_ENTRY,
        KrThemeDaySessionPhase.INTRADAY_EXIT,
    } and initial.replace(second=0, microsecond=0) != current.replace(second=0, microsecond=0):
        raise InvalidKrThemeDaySessionSupervisorError
