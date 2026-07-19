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
from tests.test_kr_theme_day_shadow_entry import OBSERVED as LATER_OBSERVED
from tests.test_kr_theme_day_shadow_entry import _signal as later_signal
from tests.test_kr_theme_day_trial import _calendar_evidence
from tests.test_kr_theme_day_trial_terminal import CLOSED_AT
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_review_store import KrThemeDayReviewStore
from trading_agent.kr_theme_day_session_audit import KrThemeDaySessionPhase
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore
from trading_agent.kr_theme_day_session_manifest import (
    KrThemeDaySessionManifest,
    KrThemeDaySessionPaths,
    build_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_session_source_state import (
    resolve_kr_theme_day_session_source_state,
)
from trading_agent.kr_theme_day_session_supervisor import (
    KrThemeDaySessionRuntime,
    run_kr_theme_day_session_tick,
)
from trading_agent.kr_theme_day_shadow_entry import project_kr_theme_day_shadow_entry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore

KST = dt.timezone(dt.timedelta(hours=9))


def test_fixture_tick_runs_all_real_intraday_children_end_to_end(tmp_path: Path) -> None:
    # Given
    manifest = _manifest(tmp_path)
    assert append_opportunity_snapshot(manifest.paths.opportunity_outbox, _opportunity()) is True
    manifest.paths.opportunity_outbox.chmod(0o600)
    first_at = dt.datetime(2026, 7, 20, 9, 4, 4, tzinfo=KST)
    replay_at = dt.datetime(2026, 7, 20, 9, 4, 30, tzinfo=KST)

    # When
    first = run_kr_theme_day_session_tick(
        manifest,
        first_at,
        KrThemeDaySessionRuntime.production(clock=lambda: first_at),
    )
    replay = run_kr_theme_day_session_tick(
        manifest,
        replay_at,
        KrThemeDaySessionRuntime.production(clock=lambda: replay_at),
    )

    # Then
    paths = manifest.paths
    assert first.blocked_phase is None
    assert replay.completed_phases == ()
    assert len(KisKrMarketReceiptStore(paths.receipt_store).receipts()) == 3
    assert len(KrThemeDayShadowEntryStore(paths.entry_store).entries()) == 1
    assert len(KrThemeDaySessionAuditStore(paths.audit_store).events(manifest.session_id)) == 5
    assert len(KrThemeDaySessionEvidenceStore(paths.audit_store).attestations(manifest.session_id)) == 5


def test_later_entry_does_not_change_prior_cycle_source_state(tmp_path: Path) -> None:
    # Given
    manifest = _manifest(tmp_path)
    assert append_opportunity_snapshot(manifest.paths.opportunity_outbox, _opportunity()) is True
    manifest.paths.opportunity_outbox.chmod(0o600)
    observed = dt.datetime(2026, 7, 20, 9, 4, 4, tzinfo=KST)
    _ = run_kr_theme_day_session_tick(
        manifest,
        observed,
        KrThemeDaySessionRuntime.production(clock=lambda: observed),
    )
    cycle = "2026-07-20T09:04+09:00"
    before = resolve_kr_theme_day_session_source_state(
        manifest,
        KrThemeDaySessionPhase.INTRADAY_ENTRY,
        cycle,
    )

    # When
    _ = project_kr_theme_day_shadow_entry(
        ExperimentLedgerStore(manifest.paths.experiment_ledger),
        KrThemeDayShadowEntryStore(manifest.paths.entry_store),
        later_signal(),
        filled_at=LATER_OBSERVED + dt.timedelta(seconds=1),
    )
    after = resolve_kr_theme_day_session_source_state(
        manifest,
        KrThemeDaySessionPhase.INTRADAY_ENTRY,
        cycle,
    )

    # Then
    assert after == before


def test_tick_blocks_opportunity_payload_changed_after_manifest_binding(tmp_path: Path) -> None:
    # Given
    manifest = _manifest(tmp_path)
    changed = _opportunity().model_copy(update={"valid_until": _opportunity().valid_until + dt.timedelta(seconds=1)})
    manifest.paths.opportunity_outbox.write_text(changed.model_dump_json() + "\n", encoding="utf-8")
    manifest.paths.opportunity_outbox.chmod(0o600)
    observed = dt.datetime(2026, 7, 20, 9, 4, 4, tzinfo=KST)

    # When
    result = run_kr_theme_day_session_tick(
        manifest,
        observed,
        KrThemeDaySessionRuntime.production(clock=lambda: observed),
    )

    # Then
    assert result.blocked_phase is KrThemeDaySessionPhase.INTRADAY_ENTRY
    assert KrThemeDayShadowEntryStore(manifest.paths.entry_store).entries() == ()


def test_restartable_fixture_day_reaches_censored_review_and_lifecycle(tmp_path: Path) -> None:
    # Given
    manifest = _manifest(tmp_path)
    calendar = KisKrSessionCalendarStore(manifest.paths.calendar_store)
    preopen_at = dt.datetime(2026, 7, 20, 8, 40, tzinfo=KST)
    opened_at = dt.datetime(2026, 7, 20, 9, 0, tzinfo=KST)
    eod_at = dt.datetime(2026, 7, 20, 15, 30, 7, tzinfo=KST)

    # When
    preopen = run_kr_theme_day_session_tick(
        manifest, preopen_at, KrThemeDaySessionRuntime.production(clock=lambda: preopen_at)
    )
    opened = run_kr_theme_day_session_tick(
        manifest, opened_at, KrThemeDaySessionRuntime.production(clock=lambda: opened_at)
    )
    eod = run_kr_theme_day_session_tick(manifest, eod_at, KrThemeDaySessionRuntime.production(clock=lambda: eod_at))
    current_receipt, current_snapshot = current_calendar_evidence()
    assert calendar.append(current_receipt, current_snapshot) is True
    post_at = dt.datetime(2026, 7, 20, 15, 31, tzinfo=KST)
    post = run_kr_theme_day_session_tick(
        manifest,
        post_at,
        KrThemeDaySessionRuntime.production(runner=_post_session_runner, clock=lambda: post_at),
    )

    # Then
    paths = manifest.paths
    assert all(result.blocked_phase is None for result in (preopen, opened, eod, post))
    ledger = ExperimentLedgerStore(paths.experiment_ledger)
    trial_id = ledger.multi_market_trials()[0].registration.trial_id
    assert len(ledger.multi_market_trial_events(trial_id)) == 2
    assert len(KrThemeDayReviewStore(paths.review_store).events()) == 1
    assert len(ledger.multi_market_lifecycle_events(VERSION)) == 1


def _manifest(tmp_path: Path) -> KrThemeDaySessionManifest:
    identity = _identity(tmp_path)
    receipt, snapshot = _calendar_evidence()
    assert KisKrSessionCalendarStore(identity.paths.calendar_store).append(receipt, snapshot) is True
    _ = _ledger(identity.paths.experiment_ledger, started=False)
    paths = KrThemeDaySessionPaths.model_validate(
        {
            **identity.paths.model_dump(mode="python"),
            "intraday_fixture_manifest": _fixture(tmp_path),
            "eod_fixture_manifest": _eod_fixture(tmp_path),
        }
    )
    return build_kr_theme_day_session_manifest(
        identity.model_copy(
            update={
                "strategy_version": VERSION,
                "code_version": CODE,
                "calendar_snapshot_id": snapshot.snapshot_id,
                "paths": paths,
            }
        )
    )


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

    return post_session_cli.main(command[1:], runner=child_runner, clock=lambda: CLOSED_AT)
