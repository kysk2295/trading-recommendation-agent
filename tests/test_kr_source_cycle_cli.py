from __future__ import annotations

import csv
import datetime as dt
import stat
import subprocess
from pathlib import Path

import pytest
import typer

import run_kr_source_cycle
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import KrThemeStore

CYCLE_ID = "kr-source-cycle-cli-20260715-001"
STARTED_AT = dt.datetime(
    2026,
    7,
    15,
    9,
    0,
    tzinfo=dt.timezone(dt.timedelta(hours=9)),
)


def test_cli_success_and_restart_are_idempotent_private_and_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "private-ledger-name.sqlite3"
    output = tmp_path / "private-report-directory"
    _seed_runs(database)

    run_kr_source_cycle.main(
        collection_cycle_id=CYCLE_ID,
        database=str(database),
        output_dir=str(output),
    )
    first_summary = _summary(output)
    run_kr_source_cycle.main(
        collection_cycle_id=CYCLE_ID,
        database=str(database),
        output_dir=str(output),
    )
    second_summary = _summary(output)
    terminal = capsys.readouterr().out

    store = KrThemeStore(database)
    assert len(store.cycles()) == 1
    assert store.cycles()[0].complete is True
    assert "신규 cycle: 예" in first_summary
    assert "신규 cycle: 아니오" in second_summary
    assert "최종 cycle complete: 예" in second_summary
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(_summary_path(output).stat().st_mode) == 0o600
    assert stat.S_IMODE(_coverage_path(output).stat().st_mode) == 0o600
    assert _coverage_rows(output) == [
        {
            "source": "dart",
            "status": "success",
            "record_count": "0",
            "failure_code": "",
        },
        {
            "source": "kis_ranking",
            "status": "success",
            "record_count": "0",
            "failure_code": "",
        },
        {
            "source": "news",
            "status": "success",
            "record_count": "0",
            "failure_code": "",
        },
        {
            "source": "volume_surge",
            "status": "success",
            "record_count": "0",
            "failure_code": "",
        },
    ]
    combined = first_summary + second_summary + terminal
    for private in (
        CYCLE_ID,
        str(database),
        str(output),
        "private-ledger-name",
        "private-report-directory",
        "receipt_id",
        "payload_sha256",
    ):
        assert private not in combined


def test_cli_missing_source_reports_gap_without_appending_cycle(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"
    _seed_runs(database, omitted_source=KrCatalystSource.KIS_RANKING)

    with pytest.raises(typer.Exit) as captured:
        run_kr_source_cycle.main(
            collection_cycle_id=CYCLE_ID,
            database=str(database),
            output_dir=str(output),
        )

    assert captured.value.exit_code == 1
    assert KrThemeStore(database).cycles() == ()
    assert "source run 확인: 3/4" in _summary(output)
    assert "누락 source: 1" in _summary(output)
    assert {
        "source": "kis_ranking",
        "status": "missing",
        "record_count": "0",
        "failure_code": "missing_source_run",
    } in _coverage_rows(output)


def test_cli_terminal_failed_run_appends_incomplete_cycle_and_returns_nonzero(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"
    _seed_runs(
        database,
        failed_source=KrCatalystSource.NEWS,
        failure_code="http_503",
    )

    with pytest.raises(typer.Exit) as captured:
        run_kr_source_cycle.main(
            collection_cycle_id=CYCLE_ID,
            database=str(database),
            output_dir=str(output),
        )

    cycles = KrThemeStore(database).cycles()
    assert captured.value.exit_code == 1
    assert len(cycles) == 1
    assert cycles[0].complete is False
    assert "실패 source: 1" in _summary(output)
    assert "최종 cycle complete: 아니오" in _summary(output)
    assert {
        "source": "news",
        "status": "failed",
        "record_count": "0",
        "failure_code": "http_503",
    } in _coverage_rows(output)


def test_cli_invalid_id_fails_before_database_or_report_creation(tmp_path: Path) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"

    with pytest.raises(typer.BadParameter):
        run_kr_source_cycle.main(
            collection_cycle_id="../escape",
            database=str(database),
            output_dir=str(output),
        )

    assert database.exists() is False
    assert output.exists() is False


def test_cli_redacts_unexpected_finalization_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_cause = "private-provider-payload-and-path"

    def fail_finalization(*args: object, **kwargs: object) -> None:
        raise ValueError(private_cause)

    monkeypatch.setattr(
        run_kr_source_cycle,
        "finalize_kr_source_cycle",
        fail_finalization,
    )

    with pytest.raises(typer.BadParameter) as captured:
        run_kr_source_cycle.main(
            collection_cycle_id=CYCLE_ID,
            database=str(tmp_path / "kr-theme.sqlite3"),
            output_dir=str(tmp_path / "report"),
        )

    assert private_cause not in str(captured.value)
    assert captured.value.__cause__ is None


def test_cli_redacts_private_report_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    _seed_runs(database)
    private_cause = "private-report-target-path"

    def fail_report(*args: object, **kwargs: object) -> None:
        raise OSError(private_cause)

    monkeypatch.setattr(run_kr_source_cycle, "write_private_report", fail_report)

    with pytest.raises(typer.BadParameter) as captured:
        run_kr_source_cycle.main(
            collection_cycle_id=CYCLE_ID,
            database=str(database),
            output_dir=str(tmp_path / "report"),
        )

    assert private_cause not in str(captured.value)
    assert captured.value.__cause__ is None


def test_executable_help_exposes_only_db_cycle_and_output_options() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "run_kr_source_cycle.py"

    completed = subprocess.run(
        [str(script), "--help"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0
    assert "--collection-cycle-id" in completed.stdout
    assert "--database" in completed.stdout
    assert "--output-dir" in completed.stdout
    for forbidden in ("--secret", "--fixture", "--url", "--account", "--order"):
        assert forbidden not in completed.stdout


def _seed_runs(
    database: Path,
    *,
    omitted_source: KrCatalystSource | None = None,
    failed_source: KrCatalystSource | None = None,
    failure_code: str | None = None,
) -> None:
    with KrThemeStore(database).writer() as writer:
        for source in KrCatalystSource:
            if source is omitted_source:
                continue
            status = (
                KrCoverageStatus.FAILED
                if source is failed_source
                else KrCoverageStatus.SUCCESS
            )
            offset = tuple(KrCatalystSource).index(source)
            started_at = STARTED_AT + dt.timedelta(seconds=offset)
            _ = writer.append_source_run(
                KrSourceCollectionRun(
                    source_run_id=f"{CYCLE_ID}:{source.value}",
                    collection_cycle_id=CYCLE_ID,
                    source=source,
                    adapter_version=f"{source.value}-fixture-v1",
                    started_at=started_at,
                    completed_at=started_at + dt.timedelta(minutes=1),
                    status=status,
                    record_count=0,
                    failure_code=failure_code if source is failed_source else None,
                    receipt_ids=(),
                )
            )


def _summary_path(output: Path) -> Path:
    return output / "kr_source_cycle_summary_ko.md"


def _coverage_path(output: Path) -> Path:
    return output / "kr_source_cycle_coverage.csv"


def _summary(output: Path) -> str:
    return _summary_path(output).read_text(encoding="utf-8")


def _coverage_rows(output: Path) -> list[dict[str, str]]:
    with _coverage_path(output).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
