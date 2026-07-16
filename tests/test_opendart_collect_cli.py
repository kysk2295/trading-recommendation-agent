from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
import typer

import run_opendart_collect
from trading_agent.kr_theme_models import KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

PRIVATE_COMPANY = "Private Synthetic Corp"
PRIVATE_REPORT = "Private Synthetic semiconductor contract"
PRIVATE_RECEIPT = "20260715000001"


def test_cli_collects_fixture_and_restart_is_idempotent_and_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest, raw_payload = _write_fixture(tmp_path / "input")
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    output = tmp_path / "report"

    run_opendart_collect.main(
        collection_cycle_id="kr-dart-fixture-001",
        collection_date="2026-07-15",
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(manifest),
        secret_path=None,
    )
    first_report = _report(output)
    run_opendart_collect.main(
        collection_cycle_id="kr-dart-fixture-001",
        collection_date="2026-07-15",
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(manifest),
        secret_path=None,
    )
    second_report = _report(output)
    terminal = capsys.readouterr().out

    store = KrThemeStore(database)
    assert len(store.source_receipts()) == 1
    assert len(store.catalysts()) == 2
    assert len(store.observation_receipts()) == 2
    assert len(store.source_runs()) == 1
    assert store.source_runs()[0].status is KrCoverageStatus.SUCCESS
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert "신규 receipt: 1" in first_report
    assert "신규 catalyst: 2" in first_report
    assert "재시작 no-op: 아니오" in first_report
    assert "신규 receipt: 0" in second_report
    assert "신규 catalyst: 0" in second_report
    assert "재시작 no-op: 예" in second_report
    combined = first_report + second_report + terminal
    for private in (
        PRIVATE_COMPANY,
        PRIVATE_REPORT,
        PRIVATE_RECEIPT,
        hashlib.sha256(raw_payload).hexdigest(),
        "OPENDART_API_KEY",
    ):
        assert private not in combined


def test_cli_terminal_replay_skips_fixture_credentials_and_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _ = _write_fixture(tmp_path / "input")
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    output = tmp_path / "report"
    run_opendart_collect.main(
        collection_cycle_id="kr-dart-replay-001",
        collection_date="2026-07-15",
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(manifest),
        secret_path=None,
    )

    def reject_dependency(*args: object, **kwargs: object) -> None:
        raise AssertionError("terminal replay opened a provider dependency")

    for name in (
        "load_opendart_fixture",
        "load_opendart_credentials",
        "create_opendart_http_client",
    ):
        monkeypatch.setattr(run_opendart_collect, name, reject_dependency)

    run_opendart_collect.main(
        collection_cycle_id="kr-dart-replay-001",
        collection_date="2026-07-15",
        database=str(database),
        output_dir=str(output),
        fixture_manifest=None,
        secret_path=None,
    )

    assert "재시작 no-op: 예" in _report(output)


def test_cli_invalid_input_fails_before_database_creation(tmp_path: Path) -> None:
    database = tmp_path / "kr-theme.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_opendart_collect.main(
            collection_cycle_id="kr-dart-fixture-001",
            collection_date="invalid",
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(tmp_path / "missing.json"),
            secret_path=None,
        )

    assert not database.exists()


def test_cli_preserves_failed_source_run_then_returns_nonzero(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "page-1.json").write_text(
        json.dumps({"status": "020", "message": "private provider message"}),
        encoding="utf-8",
    )
    manifest = fixture / "fixture-manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"

    with pytest.raises(typer.BadParameter) as captured:
        run_opendart_collect.main(
            collection_cycle_id="kr-dart-failed-001",
            collection_date="2026-07-15",
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(manifest),
            secret_path=None,
        )

    run = KrThemeStore(database).source_runs()[0]
    assert run.status is KrCoverageStatus.FAILED
    assert run.failure_code == "opendart_020"
    assert "private provider message" not in str(captured.value)
    assert "opendart_020" in _report(output)


def test_cli_rejects_fixture_with_secret_path_before_database(tmp_path: Path) -> None:
    database = tmp_path / "kr-theme.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_opendart_collect.main(
            collection_cycle_id="kr-dart-fixture-001",
            collection_date="2026-07-15",
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(tmp_path / "fixture.json"),
            secret_path=str(tmp_path / "secret.env"),
        )

    assert not database.exists()


def test_cli_redacts_unexpected_validation_causes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _ = _write_fixture(tmp_path / "input")
    private_cause = "private-unexpected-provider-payload"

    def fail_collection(*args: object, **kwargs: object) -> None:
        raise ValueError(private_cause)

    monkeypatch.setattr(
        run_opendart_collect,
        "collect_opendart_disclosures",
        fail_collection,
    )

    with pytest.raises(typer.BadParameter) as captured:
        run_opendart_collect.main(
            collection_cycle_id="kr-dart-fixture-001",
            collection_date="2026-07-15",
            database=str(tmp_path / "kr-theme.sqlite3"),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(manifest),
            secret_path=None,
        )

    assert private_cause not in str(captured.value)
    assert captured.value.__cause__ is None


def _write_fixture(directory: Path) -> tuple[Path, bytes]:
    directory.mkdir()
    document = {
        "status": "000",
        "message": "normal",
        "page_no": 1,
        "page_count": 100,
        "total_count": 2,
        "total_page": 1,
        "list": [
            _disclosure(PRIVATE_RECEIPT, "123456"),
            _disclosure("20260715000002", "654321"),
        ],
    }
    raw_payload = json.dumps(document, ensure_ascii=False).encode()
    (directory / "page-1.json").write_bytes(raw_payload)
    manifest = directory / "fixture-manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    return manifest, raw_payload


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "pages": [
            {
                "schema_version": 1,
                "page_no": 1,
                "received_at": "2026-07-15T09:01:00+09:00",
                "http_status": 200,
                "content_type": "application/json",
                "payload_path": "page-1.json",
            }
        ],
    }


def _disclosure(receipt_number: str, stock_code: str) -> dict[str, str]:
    return {
        "corp_cls": "K",
        "corp_name": PRIVATE_COMPANY,
        "corp_code": "00123456",
        "stock_code": stock_code,
        "report_nm": PRIVATE_REPORT,
        "rcept_no": receipt_number,
        "flr_nm": PRIVATE_COMPANY,
        "rcept_dt": "20260715",
        "rm": "",
    }


def _report(output: Path) -> str:
    return (output / "opendart_collection_summary_ko.md").read_text(
        encoding="utf-8"
    )
