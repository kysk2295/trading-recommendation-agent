from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import typer

import run_kr_same_cycle_collect
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

COLLECTION_DATE = "2026-07-16"
CYCLE_ID = "kr-same-cycle-cli-001"
FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE_DART_COMPANY = "Private Orchestrator Corp"
PRIVATE_DART_REPORT = "Private orchestrator filing"


def test_live_source_preflight_types_only_credential_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_kr_same_cycle_collect, "has_terminal_kr_source_runs", lambda *_args, **_kwargs: False)

    def reject_credentials() -> object:
        raise run_kr_same_cycle_collect.OpenDartSecretFileError(tmp_path / "missing.env")

    monkeypatch.setattr(run_kr_same_cycle_collect, "load_opendart_credentials", reject_credentials)

    with pytest.raises(run_kr_same_cycle_collect.KrSameCycleSourcePreflightError):
        run_kr_same_cycle_collect.require_kr_same_cycle_source_preflight(
            database=tmp_path / "kr-theme.sqlite3",
            collection_cycle_id=CYCLE_ID,
            collection_date=dt.date.fromisoformat(COLLECTION_DATE),
        )


def test_live_source_preflight_does_not_relabel_unrelated_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_kr_same_cycle_collect, "has_terminal_kr_source_runs", lambda *_args, **_kwargs: False)

    def reject_unrelated() -> object:
        raise RuntimeError("unexpected loader bug")

    monkeypatch.setattr(run_kr_same_cycle_collect, "load_opendart_credentials", reject_unrelated)

    with pytest.raises(RuntimeError, match="unexpected loader bug"):
        run_kr_same_cycle_collect.require_kr_same_cycle_source_preflight(
            database=tmp_path / "kr-theme.sqlite3",
            collection_cycle_id=CYCLE_ID,
            collection_date=dt.date.fromisoformat(COLLECTION_DATE),
        )


def test_fixture_cli_collects_four_sources_and_replays_without_any_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root, dart_payload = _fixture_root(tmp_path)
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    output = tmp_path / "report"

    run_kr_same_cycle_collect.main(
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        database=str(database),
        output_dir=str(output),
        fixture_root=str(fixture_root),
    )

    store = KrThemeStore(database)
    assert tuple(item.source for item in store.source_runs(CYCLE_ID)) == (
        KrCatalystSource.DART,
        KrCatalystSource.NEWS,
        KrCatalystSource.KIS_RANKING,
        KrCatalystSource.VOLUME_SURGE,
    )
    assert all(
        item.status is KrCoverageStatus.SUCCESS
        for item in store.source_runs(CYCLE_ID)
    )
    assert len(store.cycles()) == 1
    assert store.cycles()[0].complete is True
    summary = _summary(output)
    assert stat.S_IMODE(summary.stat().st_mode) == 0o600
    assert stat.S_IMODE(_coverage(output).stat().st_mode) == 0o600
    for marker in (
        CYCLE_ID,
        str(database),
        str(fixture_root),
        PRIVATE_DART_COMPANY,
        PRIVATE_DART_REPORT,
        hashlib.sha256(dart_payload).hexdigest(),
        "LS_APP_KEY",
        "OPENDART_API_KEY",
        "access_token",
    ):
        assert marker not in summary.read_text(encoding="utf-8")

    def reject_stage(*args: object, **kwargs: object) -> None:
        raise AssertionError("terminal replay invoked a stage")

    for module in (
        run_kr_same_cycle_collect.run_opendart_collect,
        run_kr_same_cycle_collect.run_ls_nws_collect,
        run_kr_same_cycle_collect.run_kis_kr_ranking_collect,
        run_kr_same_cycle_collect.run_kr_volume_surge_derive,
    ):
        monkeypatch.setattr(module, "main", reject_stage)

    run_kr_same_cycle_collect.main(
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        database=str(database),
        output_dir=str(output),
        fixture_root=None,
    )

    assert "재시작 source: 4" in _summary(output).read_text(encoding="utf-8")


def test_failed_terminal_source_creates_incomplete_cycle_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    fixture_root, _ = _fixture_root(tmp_path, dart_status="020")
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    output = tmp_path / "report"

    with pytest.raises(typer.Exit) as captured:
        run_kr_same_cycle_collect.main(
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            database=str(database),
            output_dir=str(output),
            fixture_root=str(fixture_root),
        )

    assert captured.value.exit_code == 1
    store = KrThemeStore(database)
    assert store.cycles()[0].complete is False
    assert store.source_runs(CYCLE_ID)[0].status is KrCoverageStatus.FAILED
    assert "incomplete" in _summary(output).read_text(encoding="utf-8")


def test_invalid_or_historical_production_request_opens_no_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_stage(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid production request invoked a stage")

    for module in (
        run_kr_same_cycle_collect.run_opendart_collect,
        run_kr_same_cycle_collect.run_ls_nws_collect,
        run_kr_same_cycle_collect.run_kis_kr_ranking_collect,
    ):
        monkeypatch.setattr(module, "main", reject_stage)
    monkeypatch.setattr(
        run_kr_same_cycle_collect,
        "_current_kst_date",
        lambda: dt.date(2026, 7, 17),
    )

    with pytest.raises(typer.BadParameter):
        run_kr_same_cycle_collect.main(
            collection_cycle_id="../escape",
            collection_date=COLLECTION_DATE,
            database=str(tmp_path / "invalid.sqlite3"),
            output_dir=str(tmp_path / "invalid-report"),
            fixture_root=None,
        )
    with pytest.raises(typer.BadParameter):
        run_kr_same_cycle_collect.main(
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            database=str(tmp_path / "historical.sqlite3"),
            output_dir=str(tmp_path / "historical-report"),
            fixture_root=None,
        )

    assert not (tmp_path / "invalid.sqlite3").exists()
    assert not (tmp_path / "historical.sqlite3").exists()


@pytest.mark.parametrize(
    "database_relative",
    (
        "kr_same_cycle_coverage.csv",
        "kr_same_cycle_summary_ko.md",
        "opendart/opendart_collection_summary_ko.md",
        "ls_nws/ls_nws_collection_summary_ko.md",
        "kis_kr_ranking/kis_kr_ranking_collection_summary_ko.md",
        "volume_surge/kr_volume_surge_derivation_summary_ko.md",
    ),
)
def test_cli_rejects_database_collision_with_any_report_target(
    tmp_path: Path,
    database_relative: str,
) -> None:
    fixture_root, _ = _fixture_root(tmp_path)
    output = tmp_path / "report"
    database = output / database_relative

    with pytest.raises(typer.BadParameter):
        run_kr_same_cycle_collect.main(
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            database=str(database),
            output_dir=str(output),
            fixture_root=str(fixture_root),
        )

    assert not database.exists()


def test_help_exposes_only_bounded_options() -> None:
    root = Path(__file__).parents[1]
    completed = subprocess.run(
        [str(root / "run_kr_same_cycle_collect.py"), "--help"],
        cwd=root,
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
        "--fixture-root",
        "--help",
    ):
        assert option in output
    for forbidden in (
        "--token",
        "--account",
        "--order",
        "--secret",
        "--url",
        "--force",
    ):
        assert forbidden not in output


def _fixture_root(
    tmp_path: Path,
    *,
    dart_status: str = "000",
) -> tuple[Path, bytes]:
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES / "kr_same_cycle", root)
    page = root / "opendart" / "page-1.json"
    document = json.loads(page.read_text(encoding="utf-8"))
    document["status"] = dart_status
    payload = json.dumps(document, ensure_ascii=False).encode()
    page.write_bytes(payload)
    return root, payload


def _summary(output: Path) -> Path:
    return output / "kr_same_cycle_summary_ko.md"


def _coverage(output: Path) -> Path:
    return output / "kr_same_cycle_coverage.csv"
