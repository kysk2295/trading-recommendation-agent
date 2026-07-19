from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

import run_kr_theme_day_open_smoke_verify as smoke_cli
from tests.kr_theme_day_open_smoke_support import VERIFIED_AT, production_session
from tests.test_kis_kr_market_projection import _quote_body, _receipt
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_onboarding_models import onboarding_receipt_path
from trading_agent.kr_theme_day_open_smoke import (
    InvalidKrThemeDayOpenSmokeError,
    KrThemeDayOpenSmokeEvidence,
    attest_kr_theme_day_open_smoke,
    load_kr_theme_day_open_smoke,
)
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_open_smoke_verify.py"


def test_open_smoke_cli_writes_evidence_and_replays_after_close(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    manifest_path = tmp_path / "session.json"
    evidence_path = tmp_path / "open-smoke.json"
    first_report = tmp_path / "first-report"
    replay_report = tmp_path / "replay-report"

    # When
    first = smoke_cli.main(
        _args(manifest_path, evidence_path, first_report),
        clock=lambda: VERIFIED_AT,
    )
    replay = smoke_cli.main(
        _args(manifest_path, evidence_path, replay_report),
        clock=lambda: VERIFIED_AT.replace(hour=16),
    )

    # Then
    assert (first, replay) == (0, 0)
    evidence = load_kr_theme_day_open_smoke(evidence_path)
    assert evidence.session_id == manifest.session_id
    assert stat.S_IMODE(evidence_path.stat().st_mode) == 0o600
    report = (replay_report / smoke_cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: verified" in report
    assert "evidence 신규/재사용: 0/1" in report
    assert "external mutation: 0" in report
    assert manifest.symbol not in report
    assert manifest.session_id not in report


def test_open_smoke_cli_blocks_missing_manifest_without_evidence(tmp_path: Path) -> None:
    # Given
    evidence_path = tmp_path / "open-smoke.json"

    # When
    result = smoke_cli.main(
        _args(tmp_path / "missing.json", evidence_path, tmp_path / "blocked"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not evidence_path.exists()
    report = tmp_path / "blocked" / smoke_cli.REPORT_NAME
    assert "result: blocked" in report.read_text(encoding="utf-8")


def test_open_smoke_cli_help_exposes_only_query_options() -> None:
    # Given / When
    result = subprocess.run(
        ("uv", "run", "python", str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert result.returncode == 0
    options = {token.rstrip(",") for token in result.stdout.split() if token.startswith("--")}
    assert options == {"--evidence", "--help", "--manifest", "--output-dir"}


def test_open_smoke_cli_rejects_report_evidence_path_alias(tmp_path: Path) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    manifest_path = tmp_path / "session.json"
    evidence_path = tmp_path / smoke_cli.REPORT_NAME

    # When
    result = smoke_cli.main(
        _args(manifest_path, evidence_path, tmp_path),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not evidence_path.exists()


def test_open_smoke_cli_rejects_evidence_session_store_path_alias(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    manifest_path = tmp_path / "session.json"
    assert not manifest.paths.entry_store.exists()

    # When
    result = smoke_cli.main(
        _args(manifest_path, manifest.paths.entry_store, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not manifest.paths.entry_store.exists()


def test_open_smoke_cli_rejects_report_derived_attestation_store_alias(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    attestation_store = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store).path
    original = attestation_store.read_bytes()
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / smoke_cli.REPORT_NAME).symlink_to(attestation_store)
    evidence_path = tmp_path / "open-smoke.json"

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", evidence_path, report_dir),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not evidence_path.exists()
    assert attestation_store.read_bytes() == original


def test_open_smoke_cli_rejects_evidence_derived_attestation_store_alias(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    attestation_store = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store).path
    original = attestation_store.read_bytes()

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", attestation_store, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert attestation_store.read_bytes() == original
    assert not (tmp_path / "report" / smoke_cli.REPORT_NAME).exists()


def test_open_smoke_cli_blocks_source_drift_during_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    evidence_path = tmp_path / "open-smoke.json"
    original_publish = smoke_cli.publish_kr_theme_day_open_smoke

    def publish_with_drift(path: Path, evidence: KrThemeDayOpenSmokeEvidence) -> bool:
        store = KisKrMarketReceiptStore(manifest.paths.receipt_store)
        assert store.append(_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=8)) is True
        return original_publish(path, evidence)

    monkeypatch.setattr(smoke_cli, "publish_kr_theme_day_open_smoke", publish_with_drift)

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", evidence_path, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not evidence_path.exists()
    assert "result: blocked" in (tmp_path / "report" / smoke_cli.REPORT_NAME).read_text(encoding="utf-8")


def test_open_smoke_cli_preserves_manifest_at_pending_path_alias(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    evidence = attest_kr_theme_day_open_smoke(manifest, verification, events, attestations, VERIFIED_AT)
    destination = tmp_path / "open-smoke.json"
    pending = destination.with_name(f".{destination.name}.{evidence.evidence_id}.pending")
    original_manifest = tmp_path / "session.json"
    original_receipt = onboarding_receipt_path(original_manifest)
    original_manifest.rename(pending)
    onboarding = onboarding_receipt_path(pending)
    original_receipt.rename(onboarding)

    # When
    result = smoke_cli.main(
        _args(pending, destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert pending.exists()
    assert onboarding.exists()
    assert not destination.exists()


def test_open_smoke_cli_rejects_casefolded_absent_store_alias(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    alias = manifest.paths.entry_store.with_name(manifest.paths.entry_store.name.upper())
    assert alias != manifest.paths.entry_store
    assert not alias.exists()

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", alias, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not alias.exists()


def test_open_smoke_cli_removes_final_after_final_publication_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"
    original_publish = smoke_cli.publish_private_immutable_alias

    def publish_with_drift(source: Path, destination: Path) -> bool:
        created = original_publish(source, destination)
        store = KisKrMarketReceiptStore(manifest.paths.receipt_store)
        assert store.append(_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=8)) is True
        return created

    monkeypatch.setattr(smoke_cli, "publish_private_immutable_alias", publish_with_drift)

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()


def test_open_smoke_cli_removes_final_after_publication_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"

    def reject_load(_path: Path) -> KrThemeDayOpenSmokeEvidence:
        raise InvalidKrThemeDayOpenSmokeError

    monkeypatch.setattr(smoke_cli, "load_kr_theme_day_open_smoke", reject_load)

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()


def test_open_smoke_cli_rejects_protected_store_as_report_directory(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"
    assert not manifest.paths.entry_store.exists()

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, manifest.paths.entry_store),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()
    assert not manifest.paths.entry_store.exists()


def test_open_smoke_cli_removes_new_evidence_when_success_report_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"

    def fail_report(_path: Path, _content: str) -> None:
        raise OSError

    monkeypatch.setattr(smoke_cli, "write_private_report", fail_report)

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()


def _args(manifest: Path, evidence: Path, output_dir: Path) -> tuple[str, ...]:
    return (
        "--manifest",
        str(manifest),
        "--evidence",
        str(evidence),
        "--output-dir",
        str(output_dir),
    )
