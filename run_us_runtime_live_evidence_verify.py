#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.private_report import write_private_report
from trading_agent.us_runtime_live_evidence_verifier import (
    RuntimeLiveEvidenceVerificationError,
    RuntimeLiveEvidenceVerificationRequest,
    RuntimeLiveEvidenceVerificationResult,
    verify_runtime_live_evidence,
)

REPORT_NAME = "us_runtime_live_evidence_verification_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query-only verification of supervisor live evidence across durable stores.",
    )
    parser.add_argument("--supervisor-store", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--actionability-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        supervisor = _file(args.supervisor_store)
        manifests = _directory(args.manifest_root)
        receipts = _directory(args.receipt_root)
        actionability = _file(args.actionability_store)
        result = verify_runtime_live_evidence(
            RuntimeLiveEvidenceVerificationRequest(
                supervisor,
                manifests,
                receipts,
                actionability,
            )
        )
    except (OSError, RuntimeLiveEvidenceVerificationError, TypeError, ValueError):
        _report(args.output_dir, ("result: blocked", "account/order mutation: 0"))
        return 1
    _report(args.output_dir, _details(result))
    return 0


def _details(result: RuntimeLiveEvidenceVerificationResult) -> tuple[str, ...]:
    return (
        "result: ready",
        f"completed/selected: {result.completed_attempt_count}/{result.selected_manifest_count}",
        "created/replay/artifact: "
        f"{result.created_terminal_count}/{result.replay_terminal_count}/{result.actionability_artifact_count}",
        "account/order mutation: 0",
    )


def _file(path: Path) -> Path:
    source = path.expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise RuntimeLiveEvidenceVerificationError
    return source


def _directory(path: Path) -> Path:
    source = path.expanduser().absolute()
    if source.is_symlink() or not source.is_dir():
        raise RuntimeLiveEvidenceVerificationError
    return source


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Runtime live evidence verification",
            "",
            *(f"- {detail}" for detail in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
