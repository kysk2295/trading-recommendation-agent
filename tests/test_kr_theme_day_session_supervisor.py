from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import run_kr_theme_day_lifecycle as lifecycle_cli
import run_kr_theme_day_post_session as post_session_cli
import run_kr_theme_day_reviewer as reviewer_cli
import run_kr_theme_day_trial_terminal as terminal_cli
from tests.test_kis_kr_market_collect_cli import _eod_fixture, _fixture
from tests.test_kis_kr_market_projection import _opportunity
from tests.test_kr_theme_day_lifecycle import DECIDED_AT
from tests.test_kr_theme_day_lifecycle import _calendar_evidence as current_calendar_evidence
from tests.test_kr_theme_day_reviewer import REVIEWED_AT
from tests.test_kr_theme_day_session_manifest import _identity
from tests.test_kr_theme_day_shadow_entry import CODE, VERSION, _ledger
from tests.test_kr_theme_day_trial import _calendar_evidence
from tests.test_kr_theme_day_trial_terminal import CLOSED_AT
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_review_store import KrThemeDayReviewStore
from trading_agent.kr_theme_day_session_audit import KrThemeDaySessionPhase
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_manifest import (
    KrThemeDaySessionPaths,
    build_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_session_supervisor import run_kr_theme_day_session_tick
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore

KST = dt.timezone(dt.timedelta(hours=9))


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
        runner=runner,
    )
    replay = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 9, 4, 30, tzinfo=KST),
        runner=runner,
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
    blocked = run_kr_theme_day_session_tick(manifest, now, runner=runner)
    resumed = run_kr_theme_day_session_tick(manifest, now, runner=runner)

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
        runner=lambda command: commands.append(command) or 0,
        clock=lambda: next(times),
    )

    # Then
    assert result.blocked_phase is None
    entry = commands[3]
    assert entry[entry.index("--evaluated-at") + 1] == "2026-07-20T09:04:05+09:00"


def test_fixture_tick_runs_all_real_intraday_children_end_to_end(tmp_path: Path) -> None:
    # Given
    identity = _identity(tmp_path)
    receipt, snapshot = _calendar_evidence()
    assert KisKrSessionCalendarStore(identity.paths.calendar_store).append(receipt, snapshot) is True
    _ = _ledger(identity.paths.experiment_ledger, started=False)
    assert append_opportunity_snapshot(identity.paths.opportunity_outbox, _opportunity()) is True
    identity.paths.opportunity_outbox.chmod(0o600)
    paths = KrThemeDaySessionPaths.model_validate(
        {
            **identity.paths.model_dump(mode="python"),
            "intraday_fixture_manifest": _fixture(tmp_path),
            "eod_fixture_manifest": _eod_fixture(tmp_path),
        }
    )
    manifest = build_kr_theme_day_session_manifest(
        identity.model_copy(
            update={
                "strategy_version": VERSION,
                "code_version": CODE,
                "calendar_snapshot_id": snapshot.snapshot_id,
                "paths": paths,
            }
        )
    )

    # When
    first = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 9, 4, 4, tzinfo=KST),
    )
    replay = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 9, 4, 30, tzinfo=KST),
    )

    # Then
    assert first.blocked_phase is None
    assert replay.completed_phases == ()
    assert len(KisKrMarketReceiptStore(paths.receipt_store).receipts()) == 3
    assert len(KrThemeDayShadowEntryStore(paths.entry_store).entries()) == 1
    assert len(KrThemeDaySessionAuditStore(paths.audit_store).events(manifest.session_id)) == 5


def test_restartable_fixture_day_reaches_censored_review_and_lifecycle(tmp_path: Path) -> None:
    # Given
    identity = _identity(tmp_path)
    receipt, snapshot = _calendar_evidence()
    calendar = KisKrSessionCalendarStore(identity.paths.calendar_store)
    assert calendar.append(receipt, snapshot) is True
    _ = _ledger(identity.paths.experiment_ledger, started=False)
    paths = KrThemeDaySessionPaths.model_validate(
        {
            **identity.paths.model_dump(mode="python"),
            "intraday_fixture_manifest": _fixture(tmp_path),
            "eod_fixture_manifest": _eod_fixture(tmp_path),
        }
    )
    manifest = build_kr_theme_day_session_manifest(
        identity.model_copy(
            update={
                "strategy_version": VERSION,
                "code_version": CODE,
                "calendar_snapshot_id": snapshot.snapshot_id,
                "paths": paths,
            }
        )
    )

    # When
    preopen = run_kr_theme_day_session_tick(manifest, dt.datetime(2026, 7, 20, 8, 40, tzinfo=KST))
    opened = run_kr_theme_day_session_tick(manifest, dt.datetime(2026, 7, 20, 9, 0, tzinfo=KST))
    eod = run_kr_theme_day_session_tick(manifest, dt.datetime(2026, 7, 20, 15, 30, 7, tzinfo=KST))
    current_receipt, current_snapshot = current_calendar_evidence()
    assert calendar.append(current_receipt, current_snapshot) is True
    post = run_kr_theme_day_session_tick(
        manifest,
        dt.datetime(2026, 7, 20, 15, 31, tzinfo=KST),
        runner=_post_session_runner,
    )

    # Then
    assert all(result.blocked_phase is None for result in (preopen, opened, eod, post))
    trial_id = ExperimentLedgerStore(paths.experiment_ledger).multi_market_trials()[0].registration.trial_id
    assert len(ExperimentLedgerStore(paths.experiment_ledger).multi_market_trial_events(trial_id)) == 2
    assert len(KrThemeDayReviewStore(paths.review_store).events()) == 1
    lifecycle = ExperimentLedgerStore(paths.experiment_ledger).multi_market_lifecycle_events(VERSION)
    assert len(lifecycle) == 1


def _post_session_runner(command: tuple[str, ...]) -> int:
    if Path(command[0]).name != "run_kr_theme_day_post_session.py":
        return subprocess.run(command, check=False).returncode

    def child_runner(child: tuple[str, ...]) -> int:
        name = Path(child[0]).name
        if name == "run_kr_theme_day_trial_terminal.py":
            return terminal_cli.main(child[1:], occurred_at=CLOSED_AT)
        if name == "run_kr_theme_day_reviewer.py":
            return reviewer_cli.main(child[1:], reviewed_at=REVIEWED_AT)
        if name == "run_kr_theme_day_lifecycle.py":
            return lifecycle_cli.main(child[1:], decided_at=DECIDED_AT)
        raise AssertionError(name)

    return post_session_cli.main(
        command[1:],
        runner=child_runner,
        clock=lambda: CLOSED_AT,
    )
