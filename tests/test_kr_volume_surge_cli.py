from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import typer

import run_kr_volume_surge_derive
from trading_agent.kis_kr_ranking_collection import collect_kis_kr_rankings
from trading_agent.kis_kr_ranking_fixture import load_kis_kr_ranking_fixture
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

COLLECTION_DATE = dt.date(2026, 7, 16)
KST = dt.timezone(dt.timedelta(hours=9))
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kis_kr_ranking"
PRIVATE_MARKERS = (
    "005930",
    "Synthetic Electronics",
    "kis-ranking://",
    "volume-surge://",
    "authorization",
    "appsecret",
)


def test_cli_derives_and_replays_with_redacted_mode_600_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cycle_id = "kr-volume-cli-001"
    database = tmp_path / "kr.sqlite3"
    output = tmp_path / "report"
    _seed_kis(tmp_path, database, cycle_id=cycle_id)

    run_kr_volume_surge_derive.main(
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(output),
    )
    first_report = _report(output)
    run_kr_volume_surge_derive.main(
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(output),
    )
    second_report = _report(output)
    terminal = capsys.readouterr().out

    store = KrThemeStore(database)
    volume_runs = tuple(
        run
        for run in store.source_runs(cycle_id)
        if run.source is KrCatalystSource.VOLUME_SURGE
    )
    assert len(volume_runs) == 1
    assert volume_runs[0].status is KrCoverageStatus.SUCCESS
    assert len(
        tuple(
            item
            for item in store.catalysts()
            if item.record.source is KrCatalystSource.VOLUME_SURGE
        )
    ) == 1
    assert "source 상태: success" in first_report
    assert "symbol: 1" in first_report
    assert "신규 catalyst: 1" in first_report
    assert "재시작 no-op: 아니오" in first_report
    assert "신규 catalyst: 0" in second_report
    assert "신규 observation: 0" in second_report
    assert "재시작 no-op: 예" in second_report
    combined = first_report + second_report + terminal
    for marker in PRIVATE_MARKERS:
        assert marker not in combined
    assert str(database) not in combined
    assert str(output) not in combined
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(_report_path(output).stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("cycle_id", "collection_date"),
    (
        ("../escape", "2026-07-16"),
        ("kr-valid", "invalid"),
        ("kr-valid", "2026-7-16"),
        ("x" * 117, "2026-07-16"),
    ),
)
def test_invalid_cli_input_fails_before_database_or_report(
    tmp_path: Path,
    cycle_id: str,
    collection_date: str,
) -> None:
    database = tmp_path / "missing.sqlite3"
    output = tmp_path / "report"

    with pytest.raises(typer.BadParameter):
        run_kr_volume_surge_derive.main(
            collection_cycle_id=cycle_id,
            collection_date=collection_date,
            database=str(database),
            output_dir=str(output),
        )

    assert not database.exists()
    assert not output.exists()


def test_missing_database_or_source_is_nonzero_without_report(tmp_path: Path) -> None:
    missing_database = tmp_path / "missing.sqlite3"
    with pytest.raises(typer.BadParameter):
        run_kr_volume_surge_derive.main(
            collection_cycle_id="kr-volume-missing-db-001",
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(missing_database),
            output_dir=str(tmp_path / "missing-report"),
        )
    assert not missing_database.exists()

    database = tmp_path / "empty.sqlite3"
    with KrThemeStore(database).writer():
        pass
    output = tmp_path / "source-report"
    with pytest.raises(typer.BadParameter, match="terminal"):
        run_kr_volume_surge_derive.main(
            collection_cycle_id="kr-volume-missing-source-001",
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(database),
            output_dir=str(output),
        )
    assert not output.exists()


def test_cli_rejects_symlink_or_nonprivate_database(tmp_path: Path) -> None:
    database = tmp_path / "kr.sqlite3"
    with KrThemeStore(database).writer():
        pass
    symlink = tmp_path / "linked.sqlite3"
    symlink.symlink_to(database)

    with pytest.raises(typer.BadParameter):
        run_kr_volume_surge_derive.main(
            collection_cycle_id="kr-volume-symlink-001",
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(symlink),
            output_dir=str(tmp_path / "report-symlink"),
        )

    database.chmod(0o644)
    with pytest.raises(typer.BadParameter):
        run_kr_volume_surge_derive.main(
            collection_cycle_id="kr-volume-mode-001",
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(database),
            output_dir=str(tmp_path / "report-mode"),
        )


def test_failed_derivation_writes_aggregate_report_then_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cycle_id = "kr-volume-cli-failed-001"
    database = tmp_path / "kr.sqlite3"
    output = tmp_path / "report"
    _seed_kis(tmp_path, database, cycle_id=cycle_id, average_volume="0")

    with pytest.raises(typer.BadParameter, match="zero_average_volume") as captured:
        run_kr_volume_surge_derive.main(
            collection_cycle_id=cycle_id,
            collection_date=COLLECTION_DATE.isoformat(),
            database=str(database),
            output_dir=str(output),
        )

    report = _report(output)
    terminal = capsys.readouterr().out
    assert "source 상태: failed" in report
    assert "failure code: zero_average_volume" in report
    assert "symbol: 0" in report
    for marker in PRIVATE_MARKERS:
        assert marker not in report + terminal + str(captured.value)


def test_help_exposes_only_local_bounded_options() -> None:
    result = subprocess.run(
        [sys.executable, "run_kr_volume_surge_derive.py", "--help"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    output = result.stdout + result.stderr
    for option in (
        "--collection-cycle-id",
        "--collection-date",
        "--database",
        "--output-dir",
        "--help",
    ):
        assert option in output
    for forbidden in (
        "--fixture",
        "--url",
        "--token",
        "--credential",
        "--account",
        "--order",
        "--force",
        "--mode",
    ):
        assert forbidden not in output


def _seed_kis(
    directory: Path,
    database: Path,
    *,
    cycle_id: str,
    average_volume: str | None = None,
) -> None:
    fixture_dir = directory / f"fixture-{cycle_id}"
    fixture_dir.mkdir()
    fluctuation = FIXTURE_DIR / "fluctuation-page-1.json"
    volume_document: object = json.loads(
        (FIXTURE_DIR / "volume-page-1.json").read_bytes()
    )
    assert isinstance(volume_document, dict)
    output = volume_document["output"]
    assert isinstance(output, list) and len(output) == 1
    row = cast(dict[str, str], output[0])
    if average_volume is not None:
        row["avrg_vol"] = average_volume
    (fixture_dir / "fluctuation.json").write_bytes(fluctuation.read_bytes())
    (fixture_dir / "volume.json").write_text(
        json.dumps(volume_document, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = fixture_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collection_date": COLLECTION_DATE.isoformat(),
                "pages": [
                    _fixture_page(
                        "fluctuation",
                        "fluctuation.json",
                        dt.datetime(2026, 7, 16, 10, 1, tzinfo=KST),
                    ),
                    _fixture_page(
                        "volume",
                        "volume.json",
                        dt.datetime(2026, 7, 16, 10, 2, tzinfo=KST),
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    fetcher = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)
    result = collect_kis_kr_rankings(
        fetcher,
        KrThemeStore(database),
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    )
    assert result.run.status is KrCoverageStatus.SUCCESS


def _fixture_page(
    kind: str,
    payload_path: str,
    received_at: dt.datetime,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": kind,
        "page_no": 1,
        "attempt": 1,
        "request_tr_cont": "",
        "response_tr_cont": "",
        "received_at": received_at.isoformat(),
        "http_status": 200,
        "content_type": "application/json",
        "payload_path": payload_path,
    }


def _report_path(output: Path) -> Path:
    return output / "kr_volume_surge_derivation_summary_ko.md"


def _report(output: Path) -> str:
    return _report_path(output).read_text(encoding="utf-8")
