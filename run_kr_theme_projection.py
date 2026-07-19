#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path
from typing import Final, override

import typer
from rich import print as rprint

from trading_agent.contract_outbox import (
    ContractOutboxConflictError,
    ContractOutboxFormatError,
    append_opportunity_snapshot,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.kr_theme_keyword import (
    ELIGIBLE_SOURCES,
    InvalidKrKeywordClassificationError,
    classify_kr_keyword_catalyst,
)
from trading_agent.kr_theme_models import KrThemeClassification
from trading_agent.kr_theme_projection import (
    InvalidKrThemeProjectionError,
    KrThemeOpportunityProjection,
    project_kr_theme_opportunities,
)
from trading_agent.kr_theme_projection_manifest import (
    KrThemeProjectionManifestError,
    LoadedKrThemeProjectionRun,
    load_kr_theme_projection_run,
)
from trading_agent.kr_theme_research_registration import (
    InvalidKrThemeResearchRegistrationError,
    KrThemeProjectionAuthorityRequest,
    require_registered_kr_theme_strategy,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    StoredKrCatalyst,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.private_report import write_private_report


class KrThemeProjectionRunError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme keyword projection을 안전하게 실행할 수 없습니다"


_PROJECTION_ARTIFACTS: Final = (
    Path("opportunities.v1.jsonl"),
    Path("kr_theme_projection_summary_ko.md"),
)


def main(
    run_manifest: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/projection/latest",
    experiment_ledger: str | None = None,
) -> None:
    if run_manifest is None or experiment_ledger is None:
        raise typer.BadParameter("run manifest 경로가 필요합니다")
    try:
        loaded = load_kr_theme_projection_run(Path(run_manifest))
        database_path = Path(database).expanduser().resolve(strict=False)
        experiment_ledger_path = Path(experiment_ledger).expanduser().resolve(strict=False)
        output = Path(output_dir)
        _validate_projection_targets(
            (database_path, experiment_ledger_path),
            output,
        )
        if loaded.run.runtime_code_version is None:
            raise KrThemeProjectionRunError
        _ = require_registered_kr_theme_strategy(
            ExperimentLedgerReader(experiment_ledger_path),
            KrThemeProjectionAuthorityRequest(
                strategy_version=loaded.run.producer_strategy_version,
                code_version=loaded.run.runtime_code_version,
                projected_at=loaded.run.projected_at,
            ),
        )
        store = KrThemeStore(database_path)
        if not store.is_initialized():
            raise KrThemeProjectionRunError
        cycles = tuple(item for item in store.cycles() if item.collection_cycle_id == loaded.run.collection_cycle_id)
        if len(cycles) != 1:
            raise KrThemeProjectionRunError
        cycle = cycles[0]
        observations = tuple(
            item for item in store.observations() if item.collection_cycle_id == cycle.collection_cycle_id
        )
        catalyst_by_id = {item.record.catalyst_id: item for item in store.catalysts()}
        try:
            catalysts = tuple(
                catalyst_by_id[item.catalyst_id] for item in sorted(observations, key=lambda value: value.catalyst_id)
            )
        except KeyError:
            raise KrThemeProjectionRunError from None
        generated = _classify_cycle(catalysts, loaded)
        effective = _merge_classifications(store.classifications(), generated)
        projections = project_kr_theme_opportunities(
            cycle,
            catalysts,
            observations,
            effective,
            classifier_version=loaded.rules.classifier_version,
            prompt_version=loaded.rules.prompt_version,
            classification_run_id=loaded.run.classification_run_id,
            projected_at=loaded.run.projected_at,
            validity=dt.timedelta(seconds=loaded.run.validity_seconds),
            producer_strategy_version=loaded.run.producer_strategy_version,
        )

        new_classifications = 0
        with store.writer() as writer:
            for classification in generated:
                new_classifications += int(writer.append_classification(classification))

        outbox = output / "opportunities.v1.jsonl"
        new_opportunities = 0
        if projections or outbox.exists():
            _prepare_private_outbox(outbox)
        if projections:
            new_opportunities = sum(append_opportunity_snapshot(outbox, item.opportunity) for item in projections)
        report = _report(
            loaded,
            projections,
            new_classifications=new_classifications,
            new_opportunities=new_opportunities,
        )
        write_private_report(
            output / "kr_theme_projection_summary_ko.md",
            report,
        )
    except (
        ContractOutboxConflictError,
        ContractOutboxFormatError,
        InvalidKrKeywordClassificationError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeProjectionError,
        InvalidKrThemeResearchRegistrationError,
        InvalidKrThemeSourceError,
        KrThemeConflictError,
        KrThemeProjectionManifestError,
        KrThemeProjectionRunError,
        KrThemeWriterLeaseUnavailableError,
        OSError,
        sqlite3.Error,
        UnicodeError,
        UnsupportedExperimentLedgerSchemaError,
        UnsupportedKrThemeSchemaError,
    ):
        raise typer.BadParameter(str(KrThemeProjectionRunError())) from None

    rprint(
        f"[green]완료[/green] KR classification {len(generated)}건, "
        + f"신규 classification {new_classifications}건, "
        + f"theme Opportunity {len(projections)}건, 신규 {new_opportunities}건"
    )


def _validate_projection_targets(
    databases: tuple[Path, ...],
    output_dir: Path,
) -> None:
    ledger_candidates = tuple(
        candidate
        for database in databases
        for candidate in (
            database.expanduser().resolve(strict=False),
            Path(f"{database.expanduser().resolve(strict=False)}.writer.lock"),
            Path(f"{database.expanduser().resolve(strict=False)}-journal"),
            Path(f"{database.expanduser().resolve(strict=False)}-shm"),
            Path(f"{database.expanduser().resolve(strict=False)}-wal"),
        )
    )
    ledger_targets = {candidate.expanduser().resolve(strict=False) for candidate in ledger_candidates}
    ledger_identities = {_file_identity(candidate) for candidate in ledger_candidates if candidate.exists()}
    for relative in _PROJECTION_ARTIFACTS:
        target = output_dir / relative
        if target.is_symlink() or (target.expanduser().resolve(strict=False) in ledger_targets):
            raise KrThemeProjectionRunError
        if target.exists() and _file_identity(target) in ledger_identities:
            raise KrThemeProjectionRunError


def _file_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def _prepare_private_outbox(outbox: Path) -> None:
    outbox.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            outbox,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    if outbox.is_symlink():
        raise KrThemeProjectionRunError
    outbox.chmod(0o600)


def _classify_cycle(
    catalysts: tuple[StoredKrCatalyst, ...],
    loaded: LoadedKrThemeProjectionRun,
) -> tuple[KrThemeClassification, ...]:
    return tuple(
        classify_kr_keyword_catalyst(
            catalyst,
            loaded.rules,
            classification_run_id=loaded.run.classification_run_id,
            classified_at=loaded.run.classified_at,
        )
        for catalyst in catalysts
        if catalyst.record.source in ELIGIBLE_SOURCES
    )


def _merge_classifications(
    existing: tuple[KrThemeClassification, ...],
    generated: tuple[KrThemeClassification, ...],
) -> tuple[KrThemeClassification, ...]:
    by_id = {item.classification_id: item for item in existing}
    if len(by_id) != len(existing):
        raise KrThemeConflictError
    for item in generated:
        previous = by_id.get(item.classification_id)
        if previous is not None and previous != item:
            raise KrThemeConflictError
        by_id[item.classification_id] = item
    return tuple(by_id[key] for key in sorted(by_id))


def _report(
    loaded: LoadedKrThemeProjectionRun,
    projections: tuple[KrThemeOpportunityProjection, ...],
    *,
    new_classifications: int,
    new_opportunities: int,
) -> str:
    lines = [
        "# KR Theme Keyword Opportunity 요약",
        "",
        "> 로컬 keyword baseline 관측이며 현재 진입 신호, 자동주문 또는 수익성 결과가 아닙니다.",
        "",
        f"- 수집 cycle: {loaded.run.collection_cycle_id}",
        f"- classifier version: {loaded.rules.classifier_version}",
        f"- classification 시각: {loaded.run.classified_at.isoformat()}",
        f"- projection 시각: {loaded.run.projected_at.isoformat()}",
        f"- 신규 classification: {new_classifications}",
        f"- 테마 수: {len(projections)}",
        f"- 신규 Opportunity: {new_opportunities}",
        "",
        "## Theme Components",
        "",
    ]
    if projections:
        lines.extend(
            f"- {item.state.theme_name} · 대장주 {item.state.leader_symbol} · "
            + f"신선도 {item.state.freshness_seconds}초 · "
            + f"촉매 {item.state.catalyst_count} · 매체 {item.state.publisher_count} · "
            + f"관련 종목 {len(item.state.related_symbols)}"
            for item in projections
        )
    else:
        lines.append("- 없음")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    typer.run(main)
