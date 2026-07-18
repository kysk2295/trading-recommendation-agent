from __future__ import annotations

import datetime as dt
import os
import shutil
import stat
import subprocess
from pathlib import Path

import run_kr_source_capability_registry as registry_cli
from trading_agent.kis_kr_ranking_collection import KIS_KR_RANKING_ADAPTER_VERSION
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.kr_volume_surge import KR_VOLUME_SURGE_ADAPTER_VERSION
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_kr_source_capability_registry.py"
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)
DATE = dt.date(2026, 7, 17)
AT = dt.datetime(2026, 7, 17, 0, 30, tzinfo=dt.UTC)
CYCLE_ID = "kr-health-cli-001"
VERSIONS = {
    KrCatalystSource.DART: OPENDART_ADAPTER_VERSION,
    KrCatalystSource.NEWS: LS_NWS_ADAPTER_VERSION,
    KrCatalystSource.KIS_RANKING: KIS_KR_RANKING_ADAPTER_VERSION,
    KrCatalystSource.VOLUME_SURGE: KR_VOLUME_SURGE_ADAPTER_VERSION,
}
SOURCE_ORDER = (
    KrCatalystSource.DART,
    KrCatalystSource.NEWS,
    KrCatalystSource.KIS_RANKING,
    KrCatalystSource.VOLUME_SURGE,
)


def test_help_exposes_local_projection_only() -> None:
    completed = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_environment(),
    )

    assert completed.returncode == 0
    assert "--collection-cycle-id" in completed.stdout
    assert "--collection-date" in completed.stdout
    assert "--registry" in completed.stdout
    assert "--arm" not in completed.stdout


def test_complete_cycle_appends_once_and_exact_retry_is_local(tmp_path: Path) -> None:
    database = tmp_path / "kr.sqlite3"
    _write_runs(database)
    registry = tmp_path / "registry.sqlite3"
    output = tmp_path / "report"
    arguments = _arguments(database, registry, output)

    first = registry_cli.main(arguments)
    first_report = (output / registry_cli.REPORT_NAME).read_text()
    second = registry_cli.main(arguments)
    report_path = output / registry_cli.REPORT_NAME
    second_report = report_path.read_text()

    assert first == second == 0
    assert "결과: complete" in first_report
    assert "capability appended: 4" in first_report
    assert "entitlement appended: 4" in first_report
    assert "capability appended: 0" in second_report
    assert "entitlement appended: 0" in second_report
    assert "source resolved: 4/4" in second_report
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_failed_cycle_is_incomplete_and_path_collision_blocks(tmp_path: Path) -> None:
    database = tmp_path / "kr.sqlite3"
    _write_runs(database, failed_source=KrCatalystSource.NEWS)
    output = tmp_path / "report"

    incomplete = registry_cli.main(_arguments(database, tmp_path / "registry.sqlite3", output))
    collision = registry_cli.main(_arguments(database, database, tmp_path / "collision"))

    assert incomplete == 2
    assert "결과: incomplete" in (output / registry_cli.REPORT_NAME).read_text()
    assert collision == 1


def _write_runs(path: Path, *, failed_source: KrCatalystSource | None = None) -> None:
    with KrThemeStore(path).writer() as writer:
        for source in SOURCE_ORDER:
            failed = source is failed_source
            writer.append_source_run(
                KrSourceCollectionRun(
                    source_run_id=f"{CYCLE_ID}:{source.value}",
                    collection_cycle_id=CYCLE_ID,
                    source=source,
                    adapter_version=VERSIONS[source],
                    started_at=AT,
                    completed_at=AT + dt.timedelta(seconds=1),
                    status=KrCoverageStatus.FAILED if failed else KrCoverageStatus.SUCCESS,
                    record_count=0,
                    failure_code="fixture_failure" if failed else None,
                    receipt_ids=(),
                    collection_date=DATE,
                )
            )


def _arguments(database: Path, registry: Path, output: Path) -> tuple[str, ...]:
    return (
        "--database",
        str(database),
        "--collection-cycle-id",
        CYCLE_ID,
        "--collection-date",
        DATE.isoformat(),
        "--registry",
        str(registry),
        "--output-dir",
        str(output),
    )


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
