#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.kr_source_cycle import (
    KrSourceCycleFinalization,
    finalize_kr_source_cycle,
)
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.private_report import write_private_report

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EXPECTED_SOURCES = tuple(sorted(KrCatalystSource, key=lambda item: item.value))


def main(
    collection_cycle_id: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/source_cycle/latest",
) -> None:
    if (
        collection_cycle_id is None
        or _SAFE_ID.fullmatch(collection_cycle_id) is None
    ):
        raise typer.BadParameter("유효한 collection cycle ID가 필요합니다")

    try:
        result = finalize_kr_source_cycle(
            KrThemeStore(Path(database)),
            collection_cycle_id=collection_cycle_id,
        )
    except (
        InvalidKrThemeSourceError,
        KrThemeConflictError,
        KrThemeWriterLeaseUnavailableError,
        UnsupportedKrThemeSchemaError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    except ValueError:
        raise typer.BadParameter(
            "KR source cycle 입력 또는 원장 계약이 유효하지 않습니다"
        ) from None

    output = Path(output_dir)
    try:
        write_private_report(
            output / "kr_source_cycle_coverage.csv",
            _coverage_csv(result),
        )
        write_private_report(
            output / "kr_source_cycle_summary_ko.md",
            _summary(result),
        )
    except OSError:
        raise typer.BadParameter(
            "KR source cycle 보고서를 안전하게 기록하지 못했습니다"
        ) from None

    if result.missing_sources:
        rprint(
            "[red]차단[/red] KR source run "
            + f"{len(result.source_runs)}/{len(_EXPECTED_SOURCES)} 확인"
        )
        raise typer.Exit(code=1)
    if result.cycle is None or not result.cycle.complete:
        rprint(
            "[red]불완전[/red] KR source cycle: "
            + f"실패 source {_failed_source_count(result)}개"
        )
        raise typer.Exit(code=1)
    rprint(
        "[green]완료[/green] KR source cycle: "
        + f"source {len(result.source_runs)}개, "
        + f"catalyst {_record_count(result)}건, "
        + f"신규 cycle {1 if result.appended else 0}건"
    )


def _coverage_csv(result: KrSourceCycleFinalization) -> str:
    runs = {run.source: run for run in result.source_runs}
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    _ = writer.writerow(("source", "status", "record_count", "failure_code"))
    for source in _EXPECTED_SOURCES:
        run = runs.get(source)
        if run is None:
            _ = writer.writerow((source.value, "missing", 0, "missing_source_run"))
        else:
            _ = writer.writerow(
                (
                    source.value,
                    run.status.value,
                    run.record_count,
                    run.failure_code or "",
                )
            )
    return output.getvalue()


def _summary(result: KrSourceCycleFinalization) -> str:
    finalized = result.cycle is not None
    complete = finalized and result.cycle is not None and result.cycle.complete
    return "\n".join(
        (
            "# KR Multi-Source Collection Cycle 요약",
            "",
            "> source coverage 감사이며 테마 추천이나 수익성 결과가 아닙니다.",
            "",
            f"- source run 확인: {len(result.source_runs)}/{len(_EXPECTED_SOURCES)}",
            f"- 누락 source: {len(result.missing_sources)}",
            f"- 성공 source: {_successful_source_count(result)}",
            f"- 실패 source: {_failed_source_count(result)}",
            f"- 관측 catalyst: {_record_count(result)}",
            f"- 최종 cycle 생성: {'예' if finalized else '아니오'}",
            f"- 최종 cycle complete: {'예' if complete else '아니오'}",
            f"- 신규 cycle: {'예' if result.appended else '아니오'}",
            "- provider·자격증명 호출: 없음",
            "- 현재가·LLM·외부 메시지·주문: 없음",
            "",
        )
    )


def _successful_source_count(result: KrSourceCycleFinalization) -> int:
    return sum(
        run.status is KrCoverageStatus.SUCCESS for run in result.source_runs
    )


def _failed_source_count(result: KrSourceCycleFinalization) -> int:
    return sum(run.status is KrCoverageStatus.FAILED for run in result.source_runs)


def _record_count(result: KrSourceCycleFinalization) -> int:
    return sum(run.record_count for run in result.source_runs)


if __name__ == "__main__":
    typer.run(main)
