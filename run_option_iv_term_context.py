#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.option_iv_term_context import (
    OptionIvTermContext,
    OptionIvTermContextError,
    build_option_iv_term_context,
    publish_option_iv_term_context,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME = "option_iv_term_context_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="READY option term structure에서 shadow-only IV slope context 생성"
    )
    parser.add_argument("--term-structure", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        context = build_option_iv_term_context(args.term_structure)
        _, created = publish_option_iv_term_context(args.output_dir, context)
        write_private_stable_report(
            args.output_dir / REPORT_NAME,
            _report(context, created),
        )
    except (
        InvalidPrivateStableReportError,
        OSError,
        OptionIvTermContextError,
        TypeError,
        ValueError,
    ):
        return 1
    print(
        "complete option IV term context "
        f"artifact_created={'yes' if created else 'no'}"
    )
    return 0


def _report(context: OptionIvTermContext, created: bool) -> str:
    return "\n".join(
        (
            "# Option IV Term Context",
            "",
            "> Point-in-time derivatives research context only; not a recommendation or order.",
            "",
            "- result: ready",
            f"- source feed: {context.feed.value}",
            f"- market date: {context.market_date.isoformat()}",
            f"- contract type: {context.contract_type.value}",
            f"- source expirations: {context.source_expiration_count}",
            f"- near/far days to expiry: {context.near_days_to_expiry}/{context.far_days_to_expiry}",
            f"- front minus back IV: {context.front_minus_back_iv}",
            f"- state: {context.state.value}",
            f"- artifact created: {'yes' if created else 'no'}",
            "- network access: 0",
            "- provider operation: query-only local evidence aggregation",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
