#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifestError,
    read_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_projection import (
    AlpacaSipQuoteActionabilityProjectionError,
    project_alpaca_sip_quote_actionability,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.private_report import write_private_report

REPORT_NAME = "alpaca-sip-quote-actionability-projection-ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project verified Alpaca SIP dynamic history into durable quote actionability.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt-store", type=Path, required=True)
    parser.add_argument("--actionability-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = read_alpaca_sip_quote_actionability_manifest(args.manifest)
        result = project_alpaca_sip_quote_actionability(
            manifest.base_publication,
            manifest.snapshot,
            AlpacaSipDynamicReceiptStore(args.receipt_store),
            manifest.plan,
            AlpacaSipQuoteActionabilityStore(args.actionability_store),
            scan_started_at=manifest.scan_started_at,
        )
    except (
        AlpacaSipQuoteActionabilityManifestError,
        AlpacaSipQuoteActionabilityProjectionError,
        OSError,
        TypeError,
        ValueError,
    ):
        _report(
            args.output_dir,
            ("result: blocked", "actionability append: 0", "account/order mutation: 0"),
        )
        return 1
    _report(
        args.output_dir,
        (
            "result: projected",
            f"terminal status: {result.decision.assessment.status.value}",
            f"actionability append: {'new' if result.appended else 'replay'}",
            f"derived signal: {'yes' if result.decision.derived_publication is not None else 'no'}",
            "account/order mutation: 0",
        ),
    )
    return 0


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Alpaca SIP quote actionability projection",
            "",
            "> 검증된 read-only market evidence의 Paper forward-validation 관측입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
