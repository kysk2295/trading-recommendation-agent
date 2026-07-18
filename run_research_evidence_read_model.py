#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from trading_agent.private_report import write_private_report
from trading_agent.research_evidence_artifact import (
    ResearchEvidenceArtifactError,
    write_research_evidence_artifact,
)
from trading_agent.research_evidence_read_model import (
    ResearchEvidenceReadModelError,
    build_research_evidence_read_model,
)
from trading_agent.research_evidence_request import (
    ResearchEvidenceRequestError,
    load_research_evidence_request,
)

REPORT_NAME = "research_evidence_read_model_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="canonical event-bound extraction을 local claim corroboration read model로 투영",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = load_research_evidence_request(args.input)
        model = build_research_evidence_read_model(
            request.events,
            request.extractions,
            as_of=request.as_of,
            current_window=dt.timedelta(seconds=request.current_window_seconds),
            baseline_window=dt.timedelta(seconds=request.baseline_window_seconds),
            burst_threshold_bps=request.burst_threshold_bps,
        )
        _artifact, appended = write_research_evidence_artifact(args.artifact_root, model)
    except (
        OSError,
        ResearchEvidenceArtifactError,
        ResearchEvidenceReadModelError,
        ResearchEvidenceRequestError,
        ValueError,
    ):
        _report(args.output_dir, result="blocked", details=("input validation: failed",))
        return 1
    _report(
        args.output_dir,
        result="ready",
        details=(
            "input validation: passed",
            f"artifact append: {'new' if appended else 'replay'}",
            f"source event count: {model.source_event_count}",
            f"extraction count: {model.extraction_count}",
            f"claim count: {len(model.claims)}",
            f"corroborated claim: {sum(item.corroboration_status.value == 'corroborated' for item in model.claims)}",
            f"conflicted claim: {sum(item.corroboration_status.value == 'conflicted' for item in model.claims)}",
        ),
    )
    return 0


def _report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Research evidence read model",
            "",
            "> local derived evidence only. Raw content, provider, account, and order access are disabled.",
            "",
            f"- result: {result}",
            *(f"- {detail}" for detail in details),
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
