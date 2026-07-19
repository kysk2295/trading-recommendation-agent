from __future__ import annotations

import datetime as dt
import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_session as session_cli
from tests.test_kr_theme_day_session_manifest import _identity
from tests.test_kr_theme_day_trial import _calendar_evidence
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_session.py"
KST = dt.timezone(dt.timedelta(hours=9))


def test_help_has_manifest_tick_without_authority_inputs() -> None:
    # Given / When
    result = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)

    # Then
    assert result.returncode == 0
    assert "init" in result.stdout
    assert "tick" in result.stdout
    for forbidden in ("account", "arm", "credential", "endpoint", "force", "order"):
        assert forbidden not in result.stdout.lower()


def test_init_and_preopen_tick_write_private_manifest_audit_and_report(tmp_path: Path) -> None:
    # Given
    identity = _identity(tmp_path)
    receipt, snapshot = _calendar_evidence()
    assert KisKrSessionCalendarStore(identity.paths.calendar_store).append(receipt, snapshot) is True
    manifest_path = tmp_path / "session.json"
    commands: list[tuple[str, ...]] = []

    # When
    initialized = session_cli.main(_init_args(identity, snapshot.snapshot_id, manifest_path))
    ticked = session_cli.main(
        ("tick", "--manifest", str(manifest_path), "--output-dir", str(tmp_path / "tick-report")),
        clock=lambda: dt.datetime(2026, 7, 20, 8, 40, tzinfo=KST),
        runner=lambda command: commands.append(command) or 0,
    )

    # Then
    assert (initialized, ticked) == (0, 0)
    assert len(commands) == 1
    assert Path(commands[0][0]).name == "run_kr_theme_day_trial.py"
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    report = tmp_path / "tick-report" / session_cli.REPORT_NAME
    assert "result: complete" in report.read_text(encoding="utf-8")
    assert "external account/order mutation: 0" in report.read_text(encoding="utf-8")
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


def _init_args(identity: session_cli.KrThemeDaySessionIdentity, snapshot_id: str, path: Path) -> tuple[str, ...]:
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
    return (
        "init",
        "--manifest",
        str(path),
        "--strategy-version",
        identity.strategy_version,
        "--code-version",
        identity.code_version,
        "--session-date",
        identity.session_date.isoformat(),
        "--registered-at",
        identity.registered_at.isoformat(),
        "--calendar-snapshot-id",
        snapshot_id,
        "--opportunity-id",
        identity.opportunity_id,
        "--symbol",
        identity.symbol,
        *path_args,
    )
