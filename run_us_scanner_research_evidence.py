#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.private_report import write_private_report
from trading_agent.research_evidence_artifact import (
    ResearchEvidenceArtifactError,
    write_research_evidence_artifact,
)
from trading_agent.us_scanner_research_projection import (
    UsScannerResearchProjectionError,
    project_us_scanner_research_evidence,
)

REPORT_NAME = "us_scanner_research_evidence.md"


class UsScannerResearchCliError(ValueError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US broad scanner candidate evidence를 local research read model로 투영",
    )
    parser.add_argument("--scanner-store", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _validate_targets(args.scanner_store, args.artifact_root, args.output_dir)
        model = project_us_scanner_research_evidence(args.scanner_store)
        appended = write_research_evidence_artifact(args.artifact_root, model)[1]
    except (
        OSError,
        ResearchEvidenceArtifactError,
        TypeError,
        UsScannerResearchCliError,
        UsScannerResearchProjectionError,
        ValueError,
    ):
        return 1
    unconfirmed = sum(item.corroboration_status.value == "unconfirmed" for item in model.claims)
    _report(
        args.output_dir,
        details=(
            f"evidence artifact: {'new' if appended else 'replay'}",
            f"source event count: {model.source_event_count}",
            f"extraction count: {model.extraction_count}",
            f"claim count: {len(model.claims)}",
            f"unconfirmed claim: {unconfirmed}",
        ),
    )
    return 0


def _validate_targets(
    scanner_store: Path,
    artifact_root: Path,
    output_dir: Path,
) -> None:
    store = scanner_store.expanduser().absolute().resolve(strict=False)
    artifact = artifact_root.expanduser().absolute().resolve(strict=False)
    report = (output_dir.expanduser().absolute() / REPORT_NAME).resolve(strict=False)
    if (
        artifact == store
        or report == store
        or _same_existing_file(store, artifact)
        or _same_existing_file(store, report)
    ):
        raise UsScannerResearchCliError


def _same_existing_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    left_metadata = left.stat()
    right_metadata = right.stat()
    return (left_metadata.st_dev, left_metadata.st_ino) == (
        right_metadata.st_dev,
        right_metadata.st_ino,
    )


def _report(output_dir: Path, *, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# US scanner research evidence",
            "",
            "> Factual candidate selection only. No recommendation, promotion, or order authority.",
            "",
            "- result: ready",
            *(f"- {item}" for item in details),
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
