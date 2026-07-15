from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
import typer

import run_ls_nws_collect
from trading_agent.kr_theme_models import KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures/ls_nws"
FIXTURE_MANIFEST = FIXTURE_ROOT / "fixture-manifest.json"
PRIVATE_TITLE_1 = "Synthetic 반도체 신규 공급 계약"
PRIVATE_TITLE_2 = "Synthetic 로봇 수주 확대"
PRIVATE_REALKEY_1 = "202607150901000100000001"
PRIVATE_REALKEY_2 = "202607150901010100000002"


def test_cli_collects_fixture_then_restarts_without_secret_or_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    output = tmp_path / "report"

    run_ls_nws_collect.main(
        collection_cycle_id="kr-ls-nws-cli-001",
        collection_date="2026-07-15",
        duration_seconds=60.0,
        max_frames=10,
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(FIXTURE_MANIFEST),
        secret_path=None,
    )
    first_report = _report(output)
    missing_secret = tmp_path / "must-not-be-read.env"
    run_ls_nws_collect.main(
        collection_cycle_id="kr-ls-nws-cli-001",
        collection_date="2026-07-15",
        duration_seconds=60.0,
        max_frames=10,
        database=str(database),
        output_dir=str(output),
        fixture_manifest=None,
        secret_path=str(missing_secret),
    )
    second_report = _report(output)
    terminal = capsys.readouterr().out

    store = KrThemeStore(database)
    assert len(store.source_receipts()) == 2
    assert len(store.catalysts()) == 2
    assert len(store.observation_receipts()) == 2
    assert len(store.source_runs()) == 1
    assert store.source_runs()[0].status is KrCoverageStatus.SUCCESS
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{database}.writer.lock").stat().st_mode) == 0o600
    assert stat.S_IMODE(_report_path(output).stat().st_mode) == 0o600
    assert "신규 receipt: 2" in first_report
    assert "신규 catalyst: 2" in first_report
    assert "재시작 no-op: 아니오" in first_report
    assert "신규 receipt: 0" in second_report
    assert "신규 catalyst: 0" in second_report
    assert "재시작 no-op: 예" in second_report
    combined = first_report + second_report + terminal
    for private in _private_markers():
        assert private not in combined


def test_cli_terminal_fixture_restart_does_not_reload_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"
    run_ls_nws_collect.main(
        collection_cycle_id="kr-ls-nws-cli-002",
        collection_date="2026-07-15",
        duration_seconds=60.0,
        max_frames=10,
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(FIXTURE_MANIFEST),
        secret_path=None,
    )

    def reject_manifest_load(path: Path) -> None:
        raise AssertionError(f"terminal restart loaded fixture manifest: {path.name}")

    monkeypatch.setattr(
        run_ls_nws_collect,
        "load_ls_nws_fixture",
        reject_manifest_load,
    )

    run_ls_nws_collect.main(
        collection_cycle_id="kr-ls-nws-cli-002",
        collection_date="2026-07-15",
        duration_seconds=60.0,
        max_frames=10,
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(FIXTURE_MANIFEST),
        secret_path=None,
    )

    assert "재시작 no-op: 예" in _report(output)


@pytest.mark.parametrize(
    ("cycle_id", "collection_date", "duration_seconds", "max_frames"),
    (
        (None, "2026-07-15", 60.0, 10),
        ("bad cycle", "2026-07-15", 60.0, 10),
        ("x" * 123, "2026-07-15", 60.0, 10),
        ("kr-ls-nws-cli-001", None, 60.0, 10),
        ("kr-ls-nws-cli-001", "invalid", 60.0, 10),
        ("kr-ls-nws-cli-001", "2026-7-15", 60.0, 10),
        ("kr-ls-nws-cli-001", "2026-07-15", 0.0, 10),
        ("kr-ls-nws-cli-001", "2026-07-15", 86_401.0, 10),
        ("kr-ls-nws-cli-001", "2026-07-15", 60.0, 0),
        ("kr-ls-nws-cli-001", "2026-07-15", 60.0, 100_001),
    ),
)
def test_cli_rejects_invalid_input_before_database_creation(
    tmp_path: Path,
    cycle_id: str | None,
    collection_date: str | None,
    duration_seconds: float,
    max_frames: int,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_ls_nws_collect.main(
            collection_cycle_id=cycle_id,
            collection_date=collection_date,
            duration_seconds=duration_seconds,
            max_frames=max_frames,
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(FIXTURE_MANIFEST),
            secret_path=None,
        )

    assert not database.exists()


def test_cli_rejects_fixture_with_secret_path_before_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_ls_nws_collect.main(
            collection_cycle_id="kr-ls-nws-cli-001",
            collection_date="2026-07-15",
            duration_seconds=60.0,
            max_frames=10,
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(FIXTURE_MANIFEST),
            secret_path=str(tmp_path / "secret.env"),
        )

    assert not database.exists()


def test_cli_preserves_failed_source_run_then_returns_nonzero(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    private_payload = b'{private-invalid-json:"private-payload"'
    (fixture / "frame.json").write_bytes(private_payload)
    manifest = fixture / "fixture-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "frames": [
                    {
                        "schema_version": 1,
                        "sequence": 1,
                        "received_at": "2026-07-15T09:01:01+09:00",
                        "wire_kind": "text",
                        "payload_path": "frame.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"

    with pytest.raises(typer.BadParameter) as captured:
        run_ls_nws_collect.main(
            collection_cycle_id="kr-ls-nws-failed-001",
            collection_date="2026-07-15",
            duration_seconds=60.0,
            max_frames=10,
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(manifest),
            secret_path=None,
        )

    run = KrThemeStore(database).source_runs()[0]
    assert run.status is KrCoverageStatus.FAILED
    assert run.failure_code == "invalid_json"
    combined = str(captured.value) + _report(output)
    assert "invalid_json" in combined
    assert "private-payload" not in combined
    assert hashlib.sha256(private_payload).hexdigest() not in combined


def test_cli_redacts_unexpected_validation_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_cause = "private-unexpected-provider-content"

    def fail_collection(*args: object, **kwargs: object) -> None:
        raise ValueError(private_cause)

    monkeypatch.setattr(
        run_ls_nws_collect,
        "collect_ls_nws_news",
        fail_collection,
    )

    with pytest.raises(typer.BadParameter) as captured:
        run_ls_nws_collect.main(
            collection_cycle_id="kr-ls-nws-cli-001",
            collection_date="2026-07-15",
            duration_seconds=60.0,
            max_frames=10,
            database=str(tmp_path / "kr-theme.sqlite3"),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(FIXTURE_MANIFEST),
            secret_path=None,
        )

    assert private_cause not in str(captured.value)
    assert captured.value.__cause__ is None


def test_private_report_writer_does_not_follow_predictable_temp_symlink(
    tmp_path: Path,
) -> None:
    report = tmp_path / "summary.md"
    protected = tmp_path / "protected.txt"
    protected.write_text("must-stay-unchanged", encoding="utf-8")
    legacy_temporary = report.with_name(f".{report.name}.{os.getpid()}.tmp")
    legacy_temporary.symlink_to(protected)

    run_ls_nws_collect._write_private_text(report, "new-report")

    assert protected.read_text(encoding="utf-8") == "must-stay-unchanged"
    assert report.read_text(encoding="utf-8") == "new-report"
    assert not report.is_symlink()
    assert legacy_temporary.is_symlink()


def test_cli_rejects_database_report_path_collision_before_creation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report"
    database = _report_path(output)

    with pytest.raises(typer.BadParameter):
        run_ls_nws_collect.main(
            collection_cycle_id="kr-ls-nws-cli-001",
            collection_date="2026-07-15",
            duration_seconds=60.0,
            max_frames=10,
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(FIXTURE_MANIFEST),
            secret_path=None,
        )

    assert not database.exists()


def _private_markers() -> tuple[str, ...]:
    raw_hashes = tuple(
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            FIXTURE_ROOT / "frame-000001.json",
            FIXTURE_ROOT / "frame-000002.json",
        )
    )
    return (
        PRIVATE_TITLE_1,
        PRIVATE_TITLE_2,
        PRIVATE_REALKEY_1,
        PRIVATE_REALKEY_2,
        *raw_hashes,
        "LS_APP_KEY",
        "LS_APP_SECRET",
        "access_token",
    )


def _report_path(output: Path) -> Path:
    return output / "ls_nws_collection_summary_ko.md"


def _report(output: Path) -> str:
    return _report_path(output).read_text(encoding="utf-8")
