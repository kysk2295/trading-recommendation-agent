from __future__ import annotations

import datetime as dt
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import override

from trading_agent.us_news_catalyst_day_session_audit import (
    UsNewsCatalystDaySessionEvent,
    UsNewsCatalystDaySessionEventRequest,
    UsNewsCatalystDaySessionEventStatus,
    UsNewsCatalystDaySessionPhase,
    build_us_news_catalyst_day_session_event,
)
from trading_agent.us_news_catalyst_day_session_evidence import (
    InvalidUsNewsCatalystDaySessionEvidenceError,
    UsNewsCatalystDaySessionEvidence,
    resolve_us_news_catalyst_day_session_evidence,
)
from trading_agent.us_news_catalyst_day_session_manifest import UsNewsCatalystDaySessionManifest
from trading_agent.us_news_catalyst_day_session_store import UsNewsCatalystDaySessionStore

CommandRunner = Callable[[tuple[str, ...]], int]
Clock = Callable[[], dt.datetime]
SourceStateResolver = Callable[
    [UsNewsCatalystDaySessionManifest, UsNewsCatalystDaySessionPhase, dt.datetime],
    UsNewsCatalystDaySessionEvidence | None,
]


class InvalidUsNewsCatalystDaySessionSupervisorError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day session supervisor input is invalid"


class UsNewsCatalystDaySessionActionStatus(StrEnum):
    WAITING = "waiting"
    EXECUTE = "execute"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDaySessionAction:
    status: UsNewsCatalystDaySessionActionStatus
    command: tuple[str, ...] | None
    reason_code: str | None

    def __post_init__(self) -> None:
        valid = (
            (
                self.status is UsNewsCatalystDaySessionActionStatus.WAITING
                and self.command is None
                and self.reason_code is None
            )
            or (
                self.status is UsNewsCatalystDaySessionActionStatus.EXECUTE
                and bool(self.command)
                and self.reason_code is None
            )
            or (
                self.status is UsNewsCatalystDaySessionActionStatus.BLOCKED
                and self.command is None
                and bool(self.reason_code)
            )
        )
        if not valid:
            raise InvalidUsNewsCatalystDaySessionSupervisorError


ActionResolver = Callable[
    [UsNewsCatalystDaySessionManifest, UsNewsCatalystDaySessionPhase, dt.datetime],
    UsNewsCatalystDaySessionAction,
]


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDaySessionRuntime:
    runner: CommandRunner
    clock: Clock
    source_state: SourceStateResolver
    action: ActionResolver

    @classmethod
    def production(cls) -> UsNewsCatalystDaySessionRuntime:
        from trading_agent.us_news_catalyst_day_session_commands import us_news_catalyst_day_session_action

        return cls(
            runner=lambda command: subprocess.run(command, check=False).returncode,
            clock=lambda: dt.datetime.now(dt.UTC),
            source_state=resolve_us_news_catalyst_day_session_evidence,
            action=us_news_catalyst_day_session_action,
        )


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDaySessionTickResult:
    phase: UsNewsCatalystDaySessionPhase | None
    action_status: UsNewsCatalystDaySessionActionStatus | None
    event: UsNewsCatalystDaySessionEvent | None


def run_us_news_catalyst_day_session_tick(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
    runtime: UsNewsCatalystDaySessionRuntime | None = None,
) -> UsNewsCatalystDaySessionTickResult:
    if not _aware(observed_at):
        raise InvalidUsNewsCatalystDaySessionSupervisorError
    active = UsNewsCatalystDaySessionRuntime.production() if runtime is None else runtime
    store = UsNewsCatalystDaySessionStore(manifest.paths.audit_store)
    with store.writer() as writer:
        history = writer.events(manifest.session_id)
        for phase in UsNewsCatalystDaySessionPhase:
            try:
                state = active.source_state(manifest, phase, observed_at)
            except InvalidUsNewsCatalystDaySessionEvidenceError:
                event = _event(
                    history,
                    UsNewsCatalystDaySessionEventRequest(
                        manifest.session_id,
                        phase,
                        observed_at,
                        UsNewsCatalystDaySessionEventStatus.BLOCKED,
                        None,
                        None,
                        "domain_evidence_invalid",
                    ),
                )
                _ = writer.append(event)
                return UsNewsCatalystDaySessionTickResult(phase, UsNewsCatalystDaySessionActionStatus.BLOCKED, event)
            if state is not None and _attested(history, state):
                continue
            if state is not None:
                status = (UsNewsCatalystDaySessionEventStatus.SKIPPED if state.skipped_reason is not None
                          else UsNewsCatalystDaySessionEventStatus.RECOVERED)
                event = _event(
                    history,
                    UsNewsCatalystDaySessionEventRequest(
                        manifest.session_id,
                        phase,
                        observed_at,
                        status,
                        None,
                        state.evidence_sha256,
                        state.skipped_reason,
                    ),
                )
                _ = writer.append(event)
                return UsNewsCatalystDaySessionTickResult(phase, UsNewsCatalystDaySessionActionStatus.EXECUTE, event)
            action = active.action(manifest, phase, observed_at)
            if action.status is UsNewsCatalystDaySessionActionStatus.WAITING:
                return UsNewsCatalystDaySessionTickResult(phase, action.status, None)
            if action.status is UsNewsCatalystDaySessionActionStatus.BLOCKED:
                event = _event(
                    history,
                    UsNewsCatalystDaySessionEventRequest(
                        manifest.session_id,
                        phase,
                        observed_at,
                        UsNewsCatalystDaySessionEventStatus.BLOCKED,
                        None,
                        None,
                        action.reason_code,
                    ),
                )
                _ = writer.append(event)
                return UsNewsCatalystDaySessionTickResult(phase, action.status, event)
            exit_code = _run(active.runner, action.command)
            checked_at = active.clock()
            try:
                completed = active.source_state(manifest, phase, checked_at)
            except InvalidUsNewsCatalystDaySessionEvidenceError:
                completed = None
            if completed is None:
                status = UsNewsCatalystDaySessionEventStatus.BLOCKED
                recorded_exit = exit_code
                evidence_sha256 = None
                reason_code = "phase_evidence_missing"
                result_status = UsNewsCatalystDaySessionActionStatus.BLOCKED
            elif completed.skipped_reason is not None:
                status = UsNewsCatalystDaySessionEventStatus.SKIPPED
                recorded_exit = None
                evidence_sha256 = completed.evidence_sha256
                reason_code = completed.skipped_reason
                result_status = UsNewsCatalystDaySessionActionStatus.EXECUTE
            else:
                status = UsNewsCatalystDaySessionEventStatus.COMPLETED
                recorded_exit = exit_code
                evidence_sha256 = completed.evidence_sha256
                reason_code = None
                result_status = UsNewsCatalystDaySessionActionStatus.EXECUTE
            event = _event(
                history,
                UsNewsCatalystDaySessionEventRequest(
                    manifest.session_id,
                    phase,
                    checked_at,
                    status,
                    recorded_exit,
                    evidence_sha256,
                    reason_code,
                ),
            )
            _ = writer.append(event)
            return UsNewsCatalystDaySessionTickResult(phase, result_status, event)
    return UsNewsCatalystDaySessionTickResult(None, None, None)


def _attested(
    events: tuple[UsNewsCatalystDaySessionEvent, ...],
    state: UsNewsCatalystDaySessionEvidence,
) -> bool:
    return any(
        item.phase is state.phase
        and item.evidence_sha256 == state.evidence_sha256
        and item.status is not UsNewsCatalystDaySessionEventStatus.BLOCKED
        for item in events
    )


def _event(
    history: tuple[UsNewsCatalystDaySessionEvent, ...],
    request: UsNewsCatalystDaySessionEventRequest,
) -> UsNewsCatalystDaySessionEvent:
    previous = None if not history else history[-1].event_id
    return build_us_news_catalyst_day_session_event(request, len(history) + 1, previous)


def _run(runner: CommandRunner, command: tuple[str, ...] | None) -> int:
    if command is None:
        raise InvalidUsNewsCatalystDaySessionSupervisorError
    try:
        return runner(command)
    except OSError:
        return 1


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
