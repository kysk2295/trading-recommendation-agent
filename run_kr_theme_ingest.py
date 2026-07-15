#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.kr_theme_ingest_manifest import (
    KrThemeManifestError,
    LoadedKrThemeIngest,
    load_kr_theme_ingest_manifest,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    UnsupportedKrThemeSchemaError,
)


def main(
    manifest: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/ingest/latest",
) -> None:
    if manifest is None:
        raise typer.BadParameter("manifest 경로가 필요합니다")
    try:
        loaded = load_kr_theme_ingest_manifest(Path(manifest))
        new_catalysts = 0
        new_observations = 0
        store = KrThemeStore(Path(database))
        with store.writer() as writer:
            for item in loaded.catalysts:
                result = writer.append_catalyst(
                    item.record,
                    item.observation,
                    item.raw_payload,
                )
                new_catalysts += int(result.catalyst_inserted)
                new_observations += int(result.observation_inserted)
            cycle_inserted = writer.append_cycle(loaded.cycle)
    except (
        InvalidKrThemeSourceError,
        KrThemeConflictError,
        KrThemeManifestError,
        KrThemeWriterLeaseUnavailableError,
        UnsupportedKrThemeSchemaError,
    ) as error:
        raise typer.BadParameter(str(error)) from None

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = _report(
        loaded,
        new_catalysts=new_catalysts,
        new_observations=new_observations,
        cycle_inserted=cycle_inserted,
    )
    _ = (output / "kr_theme_ingest_summary_ko.md").write_text(
        report,
        encoding="utf-8",
    )
    rprint(
        f"[green]완료[/green] KR catalyst {len(loaded.catalysts)}건, "
        + f"신규 원문 {new_catalysts}건, 신규 관측 {new_observations}건, "
        + f"완전 cycle {'예' if loaded.cycle.complete else '아니오'}"
    )


def _report(
    loaded: LoadedKrThemeIngest,
    *,
    new_catalysts: int,
    new_observations: int,
    cycle_inserted: bool,
) -> str:
    cycle = loaded.cycle
    lines = [
        "# KR Theme Catalyst Ingest 요약",
        "",
        "> synthetic/local raw-first ingest 감사이며 테마 추천이나 수익성 결과가 아닙니다.",
        "",
        f"- 수집 cycle: {cycle.collection_cycle_id}",
        f"- 시작 시각: {cycle.started_at.isoformat()}",
        f"- 완료 시각: {cycle.completed_at.isoformat()}",
        f"- 완전 cycle: {'예' if cycle.complete else '아니오'}",
        f"- 입력 원문: {len(loaded.catalysts)}",
        f"- 신규 원문: {new_catalysts}",
        f"- 신규 관측: {new_observations}",
        f"- 신규 cycle: {int(cycle_inserted)}",
        "",
        "## Source Coverage",
        "",
    ]
    lines.extend(
        f"- {coverage.source.value} · {coverage.status.value} · "
        + f"{coverage.record_count} · {coverage.failure_code or '없음'}"
        for coverage in cycle.coverage
    )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    typer.run(main)
