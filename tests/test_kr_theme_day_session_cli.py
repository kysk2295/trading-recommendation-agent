from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_session as session_cli
from tests.test_kr_theme_day_onboarding import ONBOARDED_AT, _same_cycle_opportunity
from tests.test_kr_theme_day_session_manifest import _identity
from tests.test_kr_theme_day_shadow_entry import CODE, VERSION, _ledger
from tests.test_kr_theme_day_trial import OPPORTUNITY_VERSION, _calendar_evidence
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_onboarding import onboarding_receipt_path
from trading_agent.kr_theme_day_session_manifest import (
    KrThemeDaySessionIdentity,
    KrThemeDaySessionPaths,
    build_kr_theme_day_session_manifest,
    write_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_trial import kr_theme_day_trial_id

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_session.py"


def test_help_has_manifest_tick_without_authority_inputs() -> None:
    # Given / When
    result = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)

    # Then
    assert result.returncode == 0
    assert "onboard" in result.stdout
    assert "tick" in result.stdout
    for forbidden in ("account", "arm", "credential", "endpoint", "force", "order"):
        assert forbidden not in result.stdout.lower()


def test_onboard_help_has_no_fixture_time_override() -> None:
    # Given / When
    result = subprocess.run(
        (str(SCRIPT), "onboard", "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert result.returncode == 0
    assert "fixture-onboarded-at" not in result.stdout


def test_onboard_writes_private_manifest_receipt_and_report(tmp_path: Path) -> None:
    # Given
    identity = _identity(tmp_path).model_copy(
        update={
            "strategy_version": VERSION,
            "code_version": CODE,
            "opportunity_strategy_version": OPPORTUNITY_VERSION,
            "paths": _fixture_paths(_identity(tmp_path).paths, tmp_path),
        }
    )
    receipt, snapshot = _calendar_evidence()
    assert KisKrSessionCalendarStore(identity.paths.calendar_store).append(receipt, snapshot) is True
    _ = _ledger(identity.paths.experiment_ledger, started=False)
    manifest_path = tmp_path / "session.json"
    opportunity = _same_cycle_opportunity()
    assert append_opportunity_snapshot(identity.paths.opportunity_outbox, opportunity) is True
    identity.paths.opportunity_outbox.chmod(0o600)

    # When
    first = session_cli.main(
        _onboard_args(identity, manifest_path, tmp_path / "first-report"),
        clock=lambda: ONBOARDED_AT,
    )
    replay = session_cli.main(
        _onboard_args(identity, manifest_path, tmp_path / "replay-report"),
        clock=lambda: ONBOARDED_AT.replace(hour=15),
    )

    # Then
    assert (first, replay) == (0, 0)
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(onboarding_receipt_path(manifest_path).stat().st_mode) == 0o600
    report = tmp_path / "first-report" / session_cli.REPORT_NAME
    assert "result: complete" in report.read_text(encoding="utf-8")
    assert "manifest created/reused: 1/0" in report.read_text(encoding="utf-8")
    assert "external account/order mutation: 0" in report.read_text(encoding="utf-8")
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    replay_report = tmp_path / "replay-report" / session_cli.REPORT_NAME
    assert "manifest created/reused: 0/1" in replay_report.read_text(encoding="utf-8")


def test_tick_rejects_manifest_without_onboarding_receipt(tmp_path: Path) -> None:
    # Given
    identity = _identity(tmp_path)
    manifest_path = tmp_path / "legacy-session.json"
    write_kr_theme_day_session_manifest(manifest_path, build_kr_theme_day_session_manifest(identity))

    # When
    result = session_cli.main(
        ("tick", "--manifest", str(manifest_path), "--output-dir", str(tmp_path / "blocked")),
        clock=lambda: ONBOARDED_AT,
        runner=lambda _command: 0,
    )

    # Then
    assert result == 1
    assert "result: blocked" in (tmp_path / "blocked" / session_cli.REPORT_NAME).read_text(encoding="utf-8")


def test_onboard_failure_writes_onboarding_blocked_report(tmp_path: Path) -> None:
    # Given
    identity = _identity(tmp_path)
    output_dir = tmp_path / "blocked"

    # When
    result = session_cli.main(
        _onboard_args(identity, tmp_path / "session.json", output_dir),
        clock=lambda: ONBOARDED_AT,
    )

    # Then
    report = (output_dir / session_cli.REPORT_NAME).read_text(encoding="utf-8")
    assert result == 1
    assert "# KR theme day Opportunity onboarding" in report
    assert "result: blocked" in report


def _onboard_args(
    identity: KrThemeDaySessionIdentity,
    manifest_path: Path,
    output_dir: Path,
) -> tuple[str, ...]:
    paths = identity.paths
    values = (
        ("experiment-ledger", paths.experiment_ledger),
        ("calendar-store", paths.calendar_store),
        ("opportunity-outbox", paths.opportunity_outbox),
        ("receipt-store", paths.receipt_store),
        ("entry-store", paths.entry_store),
        ("exit-store", paths.exit_store),
        ("terminal-store", paths.terminal_store),
        ("review-store", paths.review_store),
        ("audit-store", paths.audit_store),
        ("output-root", paths.output_root),
    )
    path_args = tuple(item for name, value in values for item in (f"--{name}", str(value)))
    fixture_args = (
        "--intraday-fixture-manifest",
        str(paths.intraday_fixture_manifest),
        "--eod-fixture-manifest",
        str(paths.eod_fixture_manifest),
    )
    return (
        "onboard",
        "--manifest",
        str(manifest_path),
        "--trial-id",
        kr_theme_day_trial_id(identity.session_date, identity.strategy_version),
        "--opportunity-id",
        identity.opportunity_id,
        "--output-dir",
        str(output_dir),
        *path_args,
        *fixture_args,
    )


def _fixture_paths(paths: KrThemeDaySessionPaths, tmp_path: Path) -> KrThemeDaySessionPaths:
    return paths.model_copy(
        update={
            "intraday_fixture_manifest": (tmp_path / "intraday-fixture.json").absolute(),
            "eod_fixture_manifest": (tmp_path / "eod-fixture.json").absolute(),
        }
    )
