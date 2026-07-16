#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from pathlib import Path
from typing import override

import typer
from rich import print as rprint

from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.kr_volume_surge import (
    InvalidKrVolumeSurgeSourceError,
    KrVolumeSurgeDerivationResult,
    KrVolumeSurgeSourceNotReadyError,
    derive_kr_volume_surge,
)
from trading_agent.private_report import write_private_report

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,115}$")


class _UnsafeLocalDatabaseError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme database는 현재 사용자 소유 mode 600 regular file이어야 합니다"


def main(
    collection_cycle_id: str | None = None,
    collection_date: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/volume_surge/latest",
) -> None:
    if collection_cycle_id is None or _SAFE_ID.fullmatch(collection_cycle_id) is None:
        raise typer.BadParameter("유효한 collection cycle ID가 필요합니다")
    parsed_date = _collection_date(collection_date)
    database_path = Path(database)

    try:
        _validate_private_database(database_path)
        store = KrThemeStore(database_path)
        if not store.is_initialized():
            raise _UnsafeLocalDatabaseError
        result = derive_kr_volume_surge(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=parsed_date,
        )
        upstream_run = _upstream_run(store, collection_cycle_id)
    except KrVolumeSurgeSourceNotReadyError:
        raise typer.BadParameter(
            "volume surge upstream KIS source가 아직 terminal이 아닙니다"
        ) from None
    except (
        InvalidKrThemeSourceError,
        InvalidKrVolumeSurgeSourceError,
        KrThemeConflictError,
        KrThemeWriterLeaseUnavailableError,
        UnsupportedKrThemeSchemaError,
        _UnsafeLocalDatabaseError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    except (FileNotFoundError, OSError, PermissionError):
        raise typer.BadParameter(
            "KR volume surge local database preflight에 실패했습니다"
        ) from None
    except ValueError:
        raise typer.BadParameter(
            "KR volume surge 입력 또는 source contract가 유효하지 않습니다"
        ) from None

    report_path = Path(output_dir) / "kr_volume_surge_derivation_summary_ko.md"
    write_private_report(
        report_path,
        _report(
            result,
            collection_date=parsed_date,
            upstream_receipt_count=len(upstream_run.receipt_ids),
            upstream_record_count=upstream_run.record_count,
        ),
    )
    if result.run.status is KrCoverageStatus.FAILED:
        raise typer.BadParameter(
            f"KR volume surge source run 실패: {result.run.failure_code}"
        )
    rprint(
        "[green]완료[/green] KR volume surge "
        + f"symbol {result.symbol_count}건, "
        + f"신규 catalyst {result.new_catalyst_count}건, "
        + f"신규 observation {result.new_observation_count}건"
    )


def _collection_date(value: str | None) -> dt.date:
    if value is None:
        raise typer.BadParameter("collection date가 필요합니다")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(
            "collection date는 YYYY-MM-DD여야 합니다"
        ) from None
    if parsed.isoformat() != value:
        raise typer.BadParameter("collection date는 YYYY-MM-DD여야 합니다")
    return parsed


def _validate_private_database(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except OSError:
        raise _UnsafeLocalDatabaseError from None
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.getuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise _UnsafeLocalDatabaseError


def _upstream_run(
    store: KrThemeStore,
    collection_cycle_id: str,
) -> KrSourceCollectionRun:
    runs = tuple(
        run
        for run in store.source_runs(collection_cycle_id)
        if run.source is KrCatalystSource.KIS_RANKING
    )
    if len(runs) != 1:
        raise InvalidKrVolumeSurgeSourceError
    return runs[0]


def _report(
    result: KrVolumeSurgeDerivationResult,
    *,
    collection_date: dt.date,
    upstream_receipt_count: int,
    upstream_record_count: int,
) -> str:
    run = result.run
    return "\n".join(
        (
            "# KR Volume Surge V2 Derivation 요약",
            "",
            "> 저장된 KIS ranking evidence의 로컬 파생 감사이며 추천이나 수익성 결과가 아닙니다.",
            "",
            f"- 수집 날짜: {collection_date.isoformat()}",
            f"- source 상태: {run.status.value}",
            f"- failure code: {run.failure_code or '없음'}",
            f"- upstream receipt: {upstream_receipt_count}",
            f"- upstream row: {upstream_record_count}",
            f"- symbol: {result.symbol_count}",
            f"- 신규 catalyst: {result.new_catalyst_count}",
            f"- 신규 observation: {result.new_observation_count}",
            f"- 재시작 no-op: {'예' if result.restarted else '아니오'}",
            "- provider network·credential·외부 mutation: 없음",
            "- 현재 호가·TradeSignal·주문: 없음",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
