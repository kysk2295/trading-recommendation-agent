#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///
# How to run: uv run --script run_alpaca_news_opportunity_evidence.py --help

from __future__ import annotations

import datetime as dt
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import typer

from trading_agent.alpaca_news_coverage import assess_alpaca_news_coverage
from trading_agent.alpaca_news_coverage_artifact import (
    load_alpaca_news_coverage_manifest,
    publish_alpaca_news_coverage_artifact,
)
from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageArtifact,
    AlpacaNewsCoverageAssessment,
    AlpacaNewsCoverageContractError,
    AlpacaNewsCoverageSliceStatus,
)
from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsOpportunityEvidenceError,
    project_alpaca_news_opportunity_evidence,
)
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    publish_alpaca_news_opportunity_evidence,
)
from trading_agent.alpaca_news_store import AlpacaNewsStore, AlpacaNewsStoreError
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_news_opportunity_evidence_ko.md"


@dataclass(frozen=True, slots=True)
class _ReportResult:
    assessment: AlpacaNewsCoverageAssessment
    coverage_created: bool
    evidence_created: bool
    snapshot_count: int


def main(
    manifest: str | None = None,
    database: str = "outputs/us_news/alpaca_news.sqlite3",
    output_dir: str = "outputs/us_news/alpaca-news-opportunity-evidence",
) -> None:
    if manifest is None:
        raise typer.BadParameter("private coverage manifest is required")
    try:
        manifest_path, database_path, output_root, report_path = _paths(
            Path(manifest),
            Path(database),
            Path(output_dir),
        )
        _preflight_report(report_path)
        scope = load_alpaca_news_coverage_manifest(manifest_path)
        if scope.cutoff_at > dt.datetime.now(dt.UTC):
            raise AlpacaNewsCoverageContractError
        store = AlpacaNewsStore(database_path)
        assessment = assess_alpaca_news_coverage(scope, store)
        _, coverage_created = publish_alpaca_news_coverage_artifact(
            output_root,
            AlpacaNewsCoverageArtifact(manifest=scope, assessment=assessment),
        )
        if assessment.complete:
            bundle = project_alpaca_news_opportunity_evidence(scope, assessment, store)
            _, evidence_created = publish_alpaca_news_opportunity_evidence(output_root, bundle)
            snapshot_count = len(bundle.snapshots)
        else:
            evidence_created = False
            snapshot_count = 0
        write_private_stable_report(
            report_path,
            _report(
                _ReportResult(
                    assessment=assessment,
                    coverage_created=coverage_created,
                    evidence_created=evidence_created,
                    snapshot_count=snapshot_count,
                )
            ),
        )
    except (
        AlpacaNewsCoverageContractError,
        AlpacaNewsOpportunityEvidenceError,
        AlpacaNewsStoreError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("Alpaca news Opportunity evidence state is invalid") from None
    if not assessment.complete:
        raise typer.Exit(code=2)
    typer.echo("complete Alpaca news Opportunity evidence")


def _paths(
    manifest: Path,
    database: Path,
    output_root: Path,
) -> tuple[Path, Path, Path, Path]:
    try:
        manifest_path = absolute_private_path(manifest)
        database_path = absolute_private_path(database)
        root = absolute_private_path(output_root)
        report_path = root / REPORT_NAME
        paths = (manifest_path, database_path, report_path)
        for index, left in enumerate(paths):
            for right in paths[index + 1 :]:
                if left == right or (
                    left.exists() and right.exists() and os.path.samestat(left.stat(), right.stat())
                ):
                    raise ValueError
        if root in {manifest_path, database_path}:
            raise ValueError
        return manifest_path, database_path, root, report_path
    except (OSError, RuntimeError, ValueError):
        raise typer.BadParameter("manifest, database, and output paths are invalid") from None


def _preflight_report(path: Path) -> None:
    parent = open_private_parent(path.parent, create=True)
    try:
        require_private_directory(parent)
        require_open_directory_path(path.parent, parent)
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            return
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise ValueError
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _report(result: _ReportResult) -> str:
    assessment = result.assessment
    successful = sum(item.status is AlpacaNewsCoverageSliceStatus.SUCCESS for item in assessment.slices)
    failed = sum(item.status is AlpacaNewsCoverageSliceStatus.FAILED for item in assessment.slices)
    missing = sum(item.status is AlpacaNewsCoverageSliceStatus.MISSING for item in assessment.slices)
    return "\n".join(
        (
            "# Alpaca News Opportunity Evidence",
            "",
            "> Source evidence only; no ranking, recommendation, or profitability result.",
            "",
            f"- result: {'complete' if assessment.complete else 'incomplete'}",
            f"- successful symbols: {assessment.successful_symbol_count}/{assessment.declared_symbol_count}",
            f"- completeness bps: {assessment.completeness_bps}",
            f"- successful slices: {successful}",
            f"- failed slices: {failed}",
            f"- missing slices: {missing}",
            f"- accepted article metadata: {assessment.accepted_article_count}",
            f"- evidence snapshots: {result.snapshot_count}",
            f"- coverage artifact created: {int(result.coverage_created)}",
            f"- evidence artifact created: {int(result.evidence_created)}",
            "- network access: 0",
            "- broker, account, position, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
