from __future__ import annotations

import datetime as dt
import stat
import subprocess
from pathlib import Path

import pytest

import run_kr_theme_day_session_verify as verify_cli
from tests.test_kis_kr_market_projection import _opportunity
from tests.test_kr_theme_day_onboarding import ONBOARDED_AT, _same_cycle_opportunity
from tests.test_kr_theme_day_session_e2e import _manifest
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.kr_theme_day_onboarding import (
    KrThemeDayOpportunityOnboardingRequest,
    onboard_kr_theme_day_opportunity,
)
from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEventRequest,
    KrThemeDaySessionPhaseStatus,
    build_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_supervisor import (
    KrThemeDaySessionRuntime,
    run_kr_theme_day_session_tick,
)
from trading_agent.kr_theme_day_session_verifier import (
    InvalidKrThemeDaySessionVerificationError,
    verify_kr_theme_day_session,
)
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_trial import kr_theme_day_trial_id

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_session_verify.py"
KST = dt.timezone(dt.timedelta(hours=9))


def test_verifier_accepts_attested_intraday_fixture_and_cli_report(tmp_path: Path) -> None:
    # Given
    source_manifest = _manifest(tmp_path)
    opportunity = _same_cycle_opportunity()
    assert append_opportunity_snapshot(source_manifest.paths.opportunity_outbox, opportunity) is True
    source_manifest.paths.opportunity_outbox.chmod(0o600)
    manifest_path = tmp_path / "session.json"
    manifest = onboard_kr_theme_day_opportunity(
        KrThemeDayOpportunityOnboardingRequest(
            manifest_path=manifest_path.absolute(),
            paths=source_manifest.paths,
            trial_id=kr_theme_day_trial_id(
                source_manifest.session_date,
                source_manifest.strategy_version,
            ),
            opportunity_id=opportunity.opportunity_id,
            onboarded_at=ONBOARDED_AT,
        )
    ).manifest
    observed = dt.datetime(2026, 7, 20, 9, 4, 4, tzinfo=KST)
    _ = run_kr_theme_day_session_tick(
        manifest,
        observed,
        KrThemeDaySessionRuntime.production(clock=lambda: observed),
    )

    # When
    result = verify_kr_theme_day_session(manifest)
    exit_code = verify_cli.main(
        (
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "verify-report"),
        )
    )

    # Then
    assert exit_code == 0
    assert (result.event_count, result.completed_count, result.attested_count) == (5, 5, 5)
    report = tmp_path / "verify-report" / verify_cli.REPORT_NAME
    text = report.read_text(encoding="utf-8")
    assert "result: verified" in text
    assert "verified completed phases: 5" in text
    assert manifest.symbol not in text
    assert manifest.session_id not in text
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


def test_verifier_rejects_legacy_completion_without_attestation(tmp_path: Path) -> None:
    # Given
    manifest = _manifest(tmp_path)
    now = dt.datetime(2026, 7, 20, 8, 40, tzinfo=KST)
    event = build_kr_theme_day_session_phase_event(
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
    assert KrThemeDaySessionAuditStore(manifest.paths.audit_store).append(event) is True

    # When / Then
    with pytest.raises(InvalidKrThemeDaySessionVerificationError):
        _ = verify_kr_theme_day_session(manifest)


def test_verifier_rejects_same_cycle_source_addition(tmp_path: Path) -> None:
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
    store = KrThemeDayShadowEntryStore(manifest.paths.entry_store)
    original = store.entries()[0]
    changed = original.model_copy(
        update={
            "entry_id": "f" * 64,
            "signal_id": "same-cycle-fault-injection",
            "signal_payload_sha256": "e" * 64,
            "signal_observed_at": observed + dt.timedelta(seconds=10),
            "filled_at": observed + dt.timedelta(seconds=11),
        }
    )
    assert store.append(changed) is True

    # When / Then
    with pytest.raises(InvalidKrThemeDaySessionVerificationError):
        _ = verify_kr_theme_day_session(manifest)


def test_verify_cli_help_and_missing_manifest_are_safe(tmp_path: Path) -> None:
    # Given / When
    help_result = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)
    blocked = verify_cli.main(
        (
            "--manifest",
            str(tmp_path / "missing.json"),
            "--output-dir",
            str(tmp_path / "blocked"),
        )
    )

    # Then
    assert help_result.returncode == 0
    assert blocked == 1
    for forbidden in ("account", "arm", "credential", "endpoint", "force", "order", "provider"):
        assert forbidden not in help_result.stdout.lower()
    report = tmp_path / "blocked" / verify_cli.REPORT_NAME
    assert "result: blocked" in report.read_text(encoding="utf-8")
