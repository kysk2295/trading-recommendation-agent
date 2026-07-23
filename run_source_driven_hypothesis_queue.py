#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.private_report import write_private_report
from trading_agent.source_driven_hypothesis_queue import (
    HypothesisQueueRoute,
    InvalidSourceDrivenHypothesisQueueError,
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)

REPORT_NAME = "source_driven_hypothesis_queue_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Source-backed research hypotheses를 실행권한 없는 deterministic queue로 투영"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        artifact = project_source_driven_hypothesis_queue(ExperimentLedgerReader(args.database))
        _, created = publish_source_driven_hypothesis_queue(args.artifact_root, artifact)
    except (
        InvalidExperimentLedgerSourceError,
        InvalidSourceDrivenHypothesisQueueError,
        OSError,
        UnsupportedExperimentLedgerSchemaError,
    ):
        _report(args.output_dir, ("결과: blocked", "external mutation: 0"))
        return 1
    routes = Counter(item.route for item in artifact.snapshot.items)
    _report(
        args.output_dir,
        (
            "결과: complete",
            f"queue item: {len(artifact.snapshot.items)}",
            f"evidence review: {routes[HypothesisQueueRoute.EVIDENCE_REVIEW]}",
            f"strategy design: {routes[HypothesisQueueRoute.STRATEGY_DESIGN]}",
            f"historical replay: {routes[HypothesisQueueRoute.HISTORICAL_REPLAY]}",
            f"active research: {routes[HypothesisQueueRoute.ACTIVE_RESEARCH]}",
            f"independent review: {routes[HypothesisQueueRoute.INDEPENDENT_REVIEW]}",
            f"recovery: {routes[HypothesisQueueRoute.RECOVERY]}",
            f"queue artifact 신규/재사용: {int(created)}/{int(not created)}",
            "lifecycle authority: false",
            "allocation authority: false",
            "order authority: false",
            "external mutation: 0",
        ),
    )
    return 0


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Source-driven hypothesis queue",
            "",
            "> immutable research lineage를 다음 실험 작업으로 routing한 결과입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
