#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from trading_agent.kr_keyword_research_projection import (
    KrKeywordResearchProjectionError,
    project_kr_keyword_research_evidence,
)
from trading_agent.kr_theme_projection_manifest import (
    KrThemeProjectionManifestError,
    load_kr_theme_projection_run,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeReader,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.private_report import write_private_report
from trading_agent.research_evidence_artifact import (
    ResearchEvidenceArtifactError,
    write_research_evidence_artifact,
)

REPORT_NAME = "kr_research_evidence_ko.md"


class KrResearchEvidenceCliError(ValueError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KR DART/LS normalized keyword evidence를 local research read model로 투영",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        database = _private_database(args.database)
        _validate_targets(database, args.artifact_root, args.output_dir)
    except (KrResearchEvidenceCliError, OSError, TypeError, ValueError):
        return 1
    try:
        loaded = load_kr_theme_projection_run(args.run_manifest)
        reader = KrThemeReader(database)
        if not reader.is_initialized():
            raise KrResearchEvidenceCliError
        model = project_kr_keyword_research_evidence(
            reader,
            collection_cycle_id=loaded.run.collection_cycle_id,
            classification_run_id=loaded.run.classification_run_id,
            rules=loaded.rules,
            classified_at=loaded.run.classified_at,
            as_of=loaded.run.projected_at,
        )
        appended = None if model is None else write_research_evidence_artifact(args.artifact_root, model)[1]
    except (
        InvalidKrThemeSourceError,
        KrKeywordResearchProjectionError,
        KrResearchEvidenceCliError,
        KrThemeProjectionManifestError,
        OSError,
        ResearchEvidenceArtifactError,
        TypeError,
        UnsupportedKrThemeSchemaError,
        ValueError,
    ):
        _report(args.output_dir, result="blocked", details=("input validation: failed",))
        return 1
    details = (
        "evidence artifact: none",
        "source event count: 0",
        "extraction count: 0",
        "claim count: 0",
    )
    if model is not None:
        details = (
            f"evidence artifact: {'new' if appended else 'replay'}",
            f"source event count: {model.source_event_count}",
            f"extraction count: {model.extraction_count}",
            f"claim count: {len(model.claims)}",
            f"corroborated claim: {sum(item.corroboration_status.value == 'corroborated' for item in model.claims)}",
        )
    _report(args.output_dir, result="ready", details=details)
    return 0


def _private_database(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    if candidate != candidate.resolve(strict=True):
        raise KrResearchEvidenceCliError
    metadata = candidate.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise KrResearchEvidenceCliError
    return candidate


def _validate_targets(database: Path, artifact_root: Path, output_dir: Path) -> None:
    report = (output_dir.expanduser().absolute() / REPORT_NAME).resolve(strict=False)
    artifact = artifact_root.expanduser().absolute().resolve(strict=False)
    if (
        report == database
        or artifact == database
        or report.is_symlink()
        or artifact.is_symlink()
        or _same_existing_file(database, report)
        or _same_existing_file(database, artifact)
    ):
        raise KrResearchEvidenceCliError


def _same_existing_file(left: Path, right: Path) -> bool:
    if not right.exists():
        return False
    left_metadata = left.stat()
    right_metadata = right.stat()
    return (left_metadata.st_dev, left_metadata.st_ino) == (
        right_metadata.st_dev,
        right_metadata.st_ino,
    )


def _report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# KR research evidence",
            "",
            "> Local normalized keyword evidence only. No recommendation, promotion, or order authority.",
            "",
            f"- result: {result}",
            *(f"- {item}" for item in details),
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
