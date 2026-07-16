from __future__ import annotations

import datetime as dt
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import typer

import run_kis_kr_ranking_collect
from trading_agent.kr_source_collection_models import KrSourceReceipt
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

COLLECTION_DATE = dt.date(2026, 7, 16)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kis_kr_ranking"
FIXTURE_MANIFEST = FIXTURE_DIR / "fixture-manifest.json"
PRIVATE_MARKERS = (
    "Synthetic Electronics",
    "005930",
    "dummy-token",
    "authorization",
    "appsecret",
    "private provider message",
)


def test_fixture_cli_collects_and_replays_with_redacted_mode_600_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "ledger" / "kr.sqlite3"
    output = tmp_path / "report"
    _reject_production_functions(monkeypatch)

    run_kis_kr_ranking_collect.main(
        collection_cycle_id="kr-kis-ranking-cli-001",
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(FIXTURE_MANIFEST),
    )
    first_report = _report(output)

    def reject_fixture(*args: object, **kwargs: object) -> None:
        raise AssertionError("terminal replay must not reopen fixture")

    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "load_kis_kr_ranking_fixture",
        reject_fixture,
    )
    run_kis_kr_ranking_collect.main(
        collection_cycle_id="kr-kis-ranking-cli-001",
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(FIXTURE_MANIFEST),
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
    assert stat.S_IMODE((output / "kis_kr_ranking_collection_summary_ko.md").stat().st_mode) == 0o600
    assert "신규 receipt: 2" in first_report
    assert "신규 catalyst: 2" in first_report
    assert "재시작 no-op: 아니오" in first_report
    assert "신규 receipt: 0" in second_report
    assert "신규 catalyst: 0" in second_report
    assert "재시작 no-op: 예" in second_report
    combined = first_report + second_report + terminal
    for marker in PRIVATE_MARKERS:
        assert marker not in combined
    for fixture_name in ("fluctuation-page-1.json", "volume-page-1.json"):
        payload = (FIXTURE_DIR / fixture_name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() not in combined
    assert str(database) not in combined
    assert str(output) not in combined


def test_production_terminal_replay_skips_credentials_and_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kr.sqlite3"
    output = tmp_path / "report"
    run_kis_kr_ranking_collect.main(
        collection_cycle_id="kr-kis-ranking-replay-001",
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(FIXTURE_MANIFEST),
    )
    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "_current_kst_date",
        _reject_dependency,
    )
    _reject_production_functions(monkeypatch)

    run_kis_kr_ranking_collect.main(
        collection_cycle_id="kr-kis-ranking-replay-001",
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(output),
        fixture_manifest=None,
    )

    assert "재시작 no-op: 예" in _report(output)


def test_production_orphan_restart_skips_date_credentials_and_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cycle_id = "kr-kis-ranking-orphan-001"
    database = tmp_path / "kr.sqlite3"
    output = tmp_path / "report"
    payload = b"{}"
    store = KrThemeStore(database)
    with store.writer() as writer:
        _ = writer.append_source_receipt(
            KrSourceReceipt(
                source_run_id=f"{cycle_id}:kis_ranking",
                source=KrCatalystSource.KIS_RANKING,
                request_key="kis-kr:fluctuation:p1:a1:rq-:rs-",
                received_at=dt.datetime(2026, 7, 16, tzinfo=dt.UTC),
                http_status=200,
                content_type="application/json",
                payload_sha256=hashlib.sha256(payload).hexdigest(),
            ),
            payload,
        )
    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "_current_kst_date",
        _reject_dependency,
    )
    _reject_production_functions(monkeypatch)

    with pytest.raises(typer.BadParameter, match="incomplete_restart"):
        run_kis_kr_ranking_collect.main(
            collection_cycle_id=cycle_id,
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(database),
            output_dir=str(output),
            fixture_manifest=None,
        )

    run = KrThemeStore(database).source_runs(cycle_id)[0]
    assert run.status is KrCoverageStatus.FAILED
    assert run.failure_code == "incomplete_restart"
    assert "재시작 no-op: 예" in _report(output)


@pytest.mark.parametrize(
    ("cycle_id", "collection_date"),
    [
        ("../escape", "2026-07-16"),
        ("kr-valid", "invalid"),
        ("kr-valid", "2026-7-16"),
        ("x" * 117, "2026-07-16"),
    ],
)
def test_invalid_cli_input_fails_before_database_creation(
    tmp_path: Path,
    cycle_id: str,
    collection_date: str,
) -> None:
    database = tmp_path / "kr.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_kis_kr_ranking_collect.main(
            collection_cycle_id=cycle_id,
            collection_date=collection_date,
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(FIXTURE_MANIFEST),
        )

    assert not database.exists()


def test_production_historical_date_fails_before_credentials_or_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kr.sqlite3"
    called = False

    def reject_credentials(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("credentials must not load")

    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "_current_kst_date",
        lambda: COLLECTION_DATE,
    )
    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "load_kis_credentials",
        reject_credentials,
    )

    with pytest.raises(typer.BadParameter, match="현재 KST 날짜"):
        run_kis_kr_ranking_collect.main(
            collection_cycle_id="kr-kis-ranking-production-001",
            collection_date="2026-07-15",
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=None,
        )

    assert called is False
    assert not database.exists()


def test_failed_fixture_run_writes_redacted_report_then_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    payload = b'{"private provider message":"secret symbol 005930"}'
    (fixture / "failed.json").write_bytes(payload)
    (fixture / "unused-volume.json").write_text("{}", encoding="utf-8")
    manifest = fixture / "fixture-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collection_date": "2026-07-16",
                "pages": [
                    {
                        "schema_version": 1,
                        "kind": "fluctuation",
                        "page_no": 1,
                        "attempt": 1,
                        "request_tr_cont": "",
                        "response_tr_cont": "",
                        "received_at": "2026-07-16T10:00:00+09:00",
                        "http_status": 429,
                        "content_type": "application/json",
                        "payload_path": "failed.json",
                    },
                    {
                        "schema_version": 1,
                        "kind": "volume",
                        "page_no": 1,
                        "attempt": 1,
                        "request_tr_cont": "",
                        "response_tr_cont": "",
                        "received_at": "2026-07-16T10:01:00+09:00",
                        "http_status": 200,
                        "content_type": "application/json",
                        "payload_path": "unused-volume.json",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report"
    database = tmp_path / "kr.sqlite3"
    _reject_production_functions(monkeypatch)

    with pytest.raises(typer.BadParameter) as captured:
        run_kis_kr_ranking_collect.main(
            collection_cycle_id="kr-kis-ranking-failed-001",
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(manifest),
        )

    report = _report(output)
    terminal = capsys.readouterr().out
    run = KrThemeStore(database).source_runs()[0]
    assert run.status is KrCoverageStatus.FAILED
    assert run.failure_code == "http_429"
    assert "source 상태: failed" in report
    assert "failure code: http_429" in report
    assert "private provider message" not in report + terminal + str(captured.value)
    assert "005930" not in report + terminal + str(captured.value)
    assert hashlib.sha256(payload).hexdigest() not in report + terminal


def test_unexpected_validation_error_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_cause = "private provider payload and token"

    def fail_collection(*args: object, **kwargs: object) -> None:
        raise ValueError(private_cause)

    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "collect_kis_kr_rankings",
        fail_collection,
    )

    with pytest.raises(typer.BadParameter) as captured:
        run_kis_kr_ranking_collect.main(
            collection_cycle_id="kr-kis-ranking-cli-001",
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(tmp_path / "kr.sqlite3"),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(FIXTURE_MANIFEST),
        )

    assert private_cause not in str(captured.value)
    assert captured.value.__cause__ is None


def test_help_exposes_only_bounded_options() -> None:
    completed = subprocess.run(
        [sys.executable, "run_kis_kr_ranking_collect.py", "--help"],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    output = completed.stdout + completed.stderr
    for option in (
        "--collection-cycle-id",
        "--collection-date",
        "--database",
        "--output-dir",
        "--fixture-manifest",
        "--help",
    ):
        assert option in output
    for forbidden in (
        "--url",
        "--token",
        "--account",
        "--order",
        "--mode",
        "--secret",
        "--force",
    ):
        assert forbidden not in output


def _reject_production_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "load_kis_credentials",
        "create_kis_client",
        "get_access_token",
    ):
        monkeypatch.setattr(run_kis_kr_ranking_collect, name, _reject_dependency)


def _reject_dependency(*args: object, **kwargs: object) -> None:
    raise AssertionError("local replay must not open deferred dependencies")


def _report(output: Path) -> str:
    return (output / "kis_kr_ranking_collection_summary_ko.md").read_text(
        encoding="utf-8"
    )
