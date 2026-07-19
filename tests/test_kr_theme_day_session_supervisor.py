from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from tests.test_kr_theme_day_session_manifest import _identity
from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEventRequest,
    KrThemeDaySessionPhaseStatus,
    build_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_evidence import KrThemeDaySessionSourceState
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore
from trading_agent.kr_theme_day_session_manifest import (
    KrThemeDaySessionManifest,
    build_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_session_supervisor import (
    Clock,
    CommandRunner,
    KrThemeDaySessionRuntime,
    run_kr_theme_day_session_tick,
)

KST = dt.timezone(dt.timedelta(hours=9))


def _fake_runtime(
    runner: CommandRunner,
    clock: Clock,
) -> KrThemeDaySessionRuntime:
    return KrThemeDaySessionRuntime(
        runner,
        clock,
        lambda _manifest, phase, cycle: KrThemeDaySessionSourceState(
            hashlib.sha256(f"{phase.value}|{cycle}".encode()).hexdigest(),
            1,
        ),
    )


def test_intraday_tick_runs_ordered_children_and_replay_skips_completed(tmp_path: Path) -> None:
    # Given
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        commands.append(command)
        return 0

    # When
    first = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 9, 4, tzinfo=KST),
        _fake_runtime(runner, lambda: dt.datetime(2026, 7, 20, 9, 4, tzinfo=KST)),
    )
    replay = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 9, 4, 30, tzinfo=KST),
        _fake_runtime(runner, lambda: dt.datetime(2026, 7, 20, 9, 4, 30, tzinfo=KST)),
    )

    # Then
    assert first.completed_phases == (
        KrThemeDaySessionPhase.REGISTER,
        KrThemeDaySessionPhase.START,
        KrThemeDaySessionPhase.INTRADAY_COLLECT,
        KrThemeDaySessionPhase.INTRADAY_ENTRY,
        KrThemeDaySessionPhase.INTRADAY_EXIT,
    )
    assert replay.completed_phases == ()
    assert [Path(command[0]).name for command in commands] == [
        "run_kr_theme_day_trial.py",
        "run_kr_theme_day_trial.py",
        "run_kis_kr_market_collect.py",
        "run_kr_theme_day_intraday.py",
        "run_kr_theme_day_shadow_exit.py",
    ]


def test_failed_phase_stops_later_children_and_restart_resumes_it(tmp_path: Path) -> None:
    # Given
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    calls: list[str] = []
    fail_once = True

    def runner(command: tuple[str, ...]) -> int:
        nonlocal fail_once
        name = Path(command[0]).name
        calls.append(name)
        if name == "run_kis_kr_market_collect.py" and fail_once:
            fail_once = False
            return 1
        return 0

    now = dt.datetime(2026, 7, 20, 9, 5, tzinfo=KST)

    # When
    runtime = _fake_runtime(runner, lambda: now)
    blocked = run_kr_theme_day_session_tick(manifest, now, runtime)
    resumed = run_kr_theme_day_session_tick(manifest, now, runtime)

    # Then
    assert blocked.blocked_phase is KrThemeDaySessionPhase.INTRADAY_COLLECT
    assert resumed.blocked_phase is None
    assert calls[-3:] == [
        "run_kis_kr_market_collect.py",
        "run_kr_theme_day_intraday.py",
        "run_kr_theme_day_shadow_exit.py",
    ]
    events = KrThemeDaySessionAuditStore(manifest.paths.audit_store).events(manifest.session_id)
    collect_exits = tuple(event.exit_code for event in events if event.phase is KrThemeDaySessionPhase.INTRADAY_COLLECT)
    assert collect_exits == (1, 0)


def test_entry_uses_fresh_time_observed_after_collection(tmp_path: Path) -> None:
    # Given
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    commands: list[tuple[str, ...]] = []
    times = iter(
        (
            dt.datetime(2026, 7, 20, 9, 4, 2, tzinfo=KST),
            dt.datetime(2026, 7, 20, 9, 4, 3, tzinfo=KST),
            dt.datetime(2026, 7, 20, 9, 4, 5, tzinfo=KST),
            dt.datetime(2026, 7, 20, 9, 4, 6, tzinfo=KST),
        )
    )

    # When
    result = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 9, 4, 1, tzinfo=KST),
        _fake_runtime(lambda command: commands.append(command) or 0, lambda: next(times)),
    )

    # Then
    assert result.blocked_phase is None
    entry = commands[3]
    assert entry[entry.index("--evaluated-at") + 1] == "2026-07-20T09:04:05+09:00"


def test_legacy_completed_event_without_source_attestation_is_replayed(tmp_path: Path) -> None:
    # Given
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    now = dt.datetime(2026, 7, 20, 8, 40, tzinfo=KST)
    legacy = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            manifest.session_id,
            KrThemeDaySessionPhase.REGISTER,
            "session",
            now,
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        1,
        None,
    )
    assert KrThemeDaySessionAuditStore(manifest.paths.audit_store).append(legacy) is True
    calls: list[tuple[str, ...]] = []

    # When
    result = run_kr_theme_day_session_tick(
        manifest,
        now,
        _fake_runtime(lambda command: calls.append(command) or 0, lambda: now),
    )

    # Then
    assert result.completed_phases == (KrThemeDaySessionPhase.REGISTER,)
    assert len(calls) == 1
    assert len(KrThemeDaySessionAuditStore(manifest.paths.audit_store).events(manifest.session_id)) == 2
    evidence = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store)
    assert len(evidence.attestations(manifest.session_id)) == 1


def test_changed_source_state_invalidates_same_cycle_completion(tmp_path: Path) -> None:
    # Given
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    now = dt.datetime(2026, 7, 20, 9, 4, tzinfo=KST)
    calls: list[tuple[str, ...]] = []
    generation = "first"

    def source_state(
        _manifest: KrThemeDaySessionManifest,
        phase: KrThemeDaySessionPhase,
        cycle: str,
    ) -> KrThemeDaySessionSourceState:
        digest = hashlib.sha256(f"{generation}|{phase.value}|{cycle}".encode()).hexdigest()
        return KrThemeDaySessionSourceState(digest, 1)

    runtime = KrThemeDaySessionRuntime(
        lambda command: calls.append(command) or 0,
        lambda: now,
        source_state,
    )
    first = run_kr_theme_day_session_tick(manifest, now, runtime)

    # When
    generation = "second"
    second = run_kr_theme_day_session_tick(manifest, now, runtime)

    # Then
    assert len(first.completed_phases) == 5
    assert len(second.completed_phases) == 5
    assert len(calls) == 10
    evidence = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store)
    assert len(evidence.attestations(manifest.session_id)) == 10
