#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override
from zoneinfo import ZoneInfo

import typer
from rich import print as rprint

import run_kis_kr_ranking_collect
import run_kr_volume_surge_derive
import run_ls_nws_collect
import run_opendart_collect
from scr_backtest.kis_intraday import MissingKisCredentialsError
from trading_agent.kis_auth import (
    KisMode,
    UnsafeSecretFileError,
    load_kis_credentials,
)
from trading_agent.kr_source_cycle_orchestrator import (
    KrSourceCycleOrchestration,
    KrSourceCycleOrchestrationError,
    has_terminal_kr_source_runs,
    orchestrate_kr_source_cycle,
)
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.ls_config import (
    InvalidLsCredentialsError,
    LsSecretEncodingError,
    LsSecretFileError,
    load_ls_credentials,
)
from trading_agent.opendart_config import (
    InvalidOpenDartCredentialsError,
    OpenDartSecretEncodingError,
    OpenDartSecretFileError,
    load_opendart_credentials,
)
from trading_agent.private_report import write_private_report

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,115}$")
_KST: Final = ZoneInfo("Asia/Seoul")
_STAGE_OUTPUT_NAMES: Final = {
    KrCatalystSource.DART: "opendart",
    KrCatalystSource.NEWS: "ls_nws",
    KrCatalystSource.KIS_RANKING: "kis_kr_ranking",
    KrCatalystSource.VOLUME_SURGE: "volume_surge",
}
_REPORT_RELATIVE_PATHS: Final = (
    Path("kr_same_cycle_coverage.csv"),
    Path("kr_same_cycle_summary_ko.md"),
    Path("opendart/opendart_collection_summary_ko.md"),
    Path("ls_nws/ls_nws_collection_summary_ko.md"),
    Path("kis_kr_ranking/kis_kr_ranking_collection_summary_ko.md"),
    Path("volume_surge/kr_volume_surge_derivation_summary_ko.md"),
)


class KrSameCycleCollectError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR same-cycle source collection을 안전하게 실행할 수 없습니다"


class KrSameCycleSourcePreflightError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR same-cycle source credential preflight가 유효하지 않습니다"


@dataclass(frozen=True, slots=True)
class _FixtureManifests:
    opendart: Path
    ls_nws: Path
    kis_kr_ranking: Path


def require_kr_same_cycle_source_preflight(
    *,
    database: Path,
    collection_cycle_id: str,
    collection_date: dt.date,
) -> None:
    if has_terminal_kr_source_runs(
        KrThemeStore(database),
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
    ):
        return
    try:
        _ = load_opendart_credentials()
        _ = load_ls_credentials()
        _ = load_kis_credentials(KisMode.LIVE)
    except (
        InvalidLsCredentialsError,
        InvalidOpenDartCredentialsError,
        LsSecretEncodingError,
        LsSecretFileError,
        MissingKisCredentialsError,
        OpenDartSecretEncodingError,
        OpenDartSecretFileError,
        OSError,
        UnicodeError,
        UnsafeSecretFileError,
    ):
        raise KrSameCycleSourcePreflightError from None


def main(
    collection_cycle_id: str | None = None,
    collection_date: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/same_cycle/latest",
    fixture_root: str | None = None,
) -> None:
    if collection_cycle_id is None or _SAFE_ID.fullmatch(collection_cycle_id) is None:
        raise typer.BadParameter("유효한 collection cycle ID가 필요합니다")
    parsed_date = _collection_date(collection_date)
    database_path = Path(database)
    output_path = Path(output_dir)
    try:
        _validate_report_targets(database_path, output_path)
    except KrSameCycleCollectError:
        raise typer.BadParameter(str(KrSameCycleCollectError())) from None
    fixtures = _fixture_manifests(fixture_root)
    store = KrThemeStore(database_path)
    try:
        terminal_replay = has_terminal_kr_source_runs(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=parsed_date,
        )
        if (
            fixtures is None
            and not terminal_replay
            and parsed_date != _current_kst_date()
        ):
            raise KrSameCycleCollectError
        result = orchestrate_kr_source_cycle(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=parsed_date,
            stage_runners=_stage_runners(
                collection_cycle_id=collection_cycle_id,
                collection_date=parsed_date,
                database=database,
                output_dir=output_path,
                fixtures=fixtures,
            ),
        )
        _write_reports(output_path, result)
    except (
        InvalidKrThemeSourceError,
        KrSameCycleCollectError,
        KrSourceCycleOrchestrationError,
        KrThemeConflictError,
        KrThemeWriterLeaseUnavailableError,
        OSError,
        UnsupportedKrThemeSchemaError,
        ValueError,
    ):
        raise typer.BadParameter(str(KrSameCycleCollectError())) from None

    if not result.cycle.complete:
        raise typer.Exit(code=1)
    rprint(
        "[green]완료[/green] KR same-cycle source collection "
        + f"source {len(result.stages)}건, 재시작 {sum(item.replayed for item in result.stages)}건"
    )


def _collection_date(value: str | None) -> dt.date:
    if value is None:
        raise typer.BadParameter("collection date가 필요합니다")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter("collection date는 YYYY-MM-DD여야 합니다") from None
    if parsed.isoformat() != value:
        raise typer.BadParameter("collection date는 YYYY-MM-DD여야 합니다")
    return parsed


def _fixture_manifests(value: str | None) -> _FixtureManifests | None:
    if value is None:
        return None
    root = Path(value)
    manifests = _FixtureManifests(
        opendart=root / "opendart" / "fixture-manifest.json",
        ls_nws=root / "ls_nws" / "fixture-manifest.json",
        kis_kr_ranking=root / "kis_kr_ranking" / "fixture-manifest.json",
    )
    if not all(
        path.is_file()
        and not path.is_symlink()
        for path in (
            manifests.opendart,
            manifests.ls_nws,
            manifests.kis_kr_ranking,
        )
    ):
        raise typer.BadParameter("fixture root contract가 유효하지 않습니다")
    return manifests


def _current_kst_date() -> dt.date:
    return dt.datetime.now(_KST).date()


def _validate_report_targets(database: Path, output_dir: Path) -> None:
    ledger_targets = {
        candidate.expanduser().resolve(strict=False)
        for candidate in (
            database,
            Path(f"{database}.writer.lock"),
            Path(f"{database}-journal"),
            Path(f"{database}-shm"),
            Path(f"{database}-wal"),
        )
    }
    report_targets = {
        (output_dir / relative).expanduser().resolve(strict=False)
        for relative in _REPORT_RELATIVE_PATHS
    }
    if ledger_targets & report_targets:
        raise KrSameCycleCollectError


def _stage_runners(
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    database: str,
    output_dir: Path,
    fixtures: _FixtureManifests | None,
) -> dict[KrCatalystSource, Callable[[], None]]:
    date = collection_date.isoformat()

    def run_opendart() -> None:
        _run_terminal_stage(
            run_opendart_collect.main,
            collection_cycle_id=collection_cycle_id,
            collection_date=date,
            database=database,
            output_dir=str(output_dir / _STAGE_OUTPUT_NAMES[KrCatalystSource.DART]),
            fixture_manifest=(
                None if fixtures is None else str(fixtures.opendart)
            ),
            secret_path=None,
        )

    def run_news() -> None:
        _run_terminal_stage(
            run_ls_nws_collect.main,
            collection_cycle_id=collection_cycle_id,
            collection_date=date,
            duration_seconds=60.0,
            max_frames=1_000,
            database=database,
            output_dir=str(output_dir / _STAGE_OUTPUT_NAMES[KrCatalystSource.NEWS]),
            fixture_manifest=(None if fixtures is None else str(fixtures.ls_nws)),
            secret_path=None,
        )

    def run_kis_ranking() -> None:
        _run_terminal_stage(
            run_kis_kr_ranking_collect.main,
            collection_cycle_id=collection_cycle_id,
            collection_date=date,
            database=database,
            output_dir=str(
                output_dir / _STAGE_OUTPUT_NAMES[KrCatalystSource.KIS_RANKING]
            ),
            fixture_manifest=(
                None if fixtures is None else str(fixtures.kis_kr_ranking)
            ),
        )

    def run_volume_surge() -> None:
        _run_terminal_stage(
            run_kr_volume_surge_derive.main,
            collection_cycle_id=collection_cycle_id,
            collection_date=date,
            database=database,
            output_dir=str(
                output_dir / _STAGE_OUTPUT_NAMES[KrCatalystSource.VOLUME_SURGE]
            ),
        )

    return {
        KrCatalystSource.DART: run_opendart,
        KrCatalystSource.NEWS: run_news,
        KrCatalystSource.KIS_RANKING: run_kis_ranking,
        KrCatalystSource.VOLUME_SURGE: run_volume_surge,
    }


def _run_terminal_stage(
    operation: Callable[..., None],
    /,
    **kwargs: object,
) -> None:
    try:
        operation(**kwargs)
    except typer.BadParameter:
        return


def _write_reports(
    output_dir: Path,
    result: KrSourceCycleOrchestration,
) -> None:
    coverage = io.StringIO(newline="")
    writer = csv.writer(coverage, lineterminator="\n")
    writer.writerow(("source", "status", "record_count", "failure_code", "replayed"))
    for stage in result.stages:
        writer.writerow(
            (
                stage.source.value,
                stage.status.value,
                stage.record_count,
                stage.failure_code or "",
                str(stage.replayed).lower(),
            )
        )
    completed = sum(
        stage.status is KrCoverageStatus.SUCCESS for stage in result.stages
    )
    failed = len(result.stages) - completed
    summary = "\n".join(
        (
            "# KR Same-Cycle Source Collection 요약",
            "",
            "> source coverage 감사이며 Opportunity, 현재 진입, shadow 체결 또는 주문 결과가 아닙니다.",
            "",
            f"- terminal source: {len(result.stages)}",
            f"- success source: {completed}",
            f"- failed source: {failed}",
            f"- 재시작 source: {sum(stage.replayed for stage in result.stages)}",
            f"- final cycle: {'complete' if result.cycle.complete else 'incomplete'}",
            f"- 신규 cycle: {'예' if result.appended else '아니오'}",
            "- provider, credential, broker payload: 보고서에 포함하지 않음",
            "",
        )
    )
    write_private_report(output_dir / "kr_same_cycle_coverage.csv", coverage.getvalue())
    write_private_report(output_dir / "kr_same_cycle_summary_ko.md", summary)


if __name__ == "__main__":
    typer.run(main)
