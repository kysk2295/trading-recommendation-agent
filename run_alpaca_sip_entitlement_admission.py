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
from typing import assert_never

from trading_agent.alpaca_sip_entitlement_admission import (
    AlpacaSipEntitlementAdmissionUnknown,
    assess_alpaca_sip_entitlement,
)
from trading_agent.alpaca_sip_entitlement_artifacts import (
    AlpacaSipEntitlementAdmissionArtifact,
    AlpacaSipEntitlementAdmissionError,
    require_private_admission_root,
    write_alpaca_sip_entitlement_artifact,
)
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamProtocolError,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore
from trading_agent.private_report import write_private_report

REPORT_NAME = "alpaca_sip_entitlement_admission_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Persisted Alpaca SIP evidence를 query-only entitlement admission으로 투영",
    )
    parser.add_argument("--stream-store", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir
    root: Path | None = None
    try:
        root = require_private_admission_root(output_dir)
        config = AlpacaSipTradeStreamConfig(
            dt.date.fromisoformat(args.market_date),
            args.symbol,
        )
        result = assess_alpaca_sip_entitlement(
            AlpacaSipTradeStreamStore(args.stream_store),
            config,
        )
        match result:
            case AlpacaSipEntitlementAdmissionArtifact():
                _, created = write_alpaca_sip_entitlement_artifact(root, result)
                _write_report(
                    root,
                    result=result.status.value,
                    reason=result.reason_code.value,
                    artifact_id=result.artifact_id,
                    created=created,
                )
                return 0 if result.status.value == "ready" else 2
            case AlpacaSipEntitlementAdmissionUnknown(reason_code=reason):
                _write_report(
                    root,
                    result="unknown",
                    reason=reason,
                    artifact_id=None,
                    created=False,
                )
                return 1
            case unreachable:
                assert_never(unreachable)
    except (
        AlpacaSipEntitlementAdmissionError,
        AlpacaSipTradeStreamProtocolError,
        OSError,
        TypeError,
        ValueError,
    ):
        if root is not None:
            _write_report(
                root,
                result="invalid",
                reason="evidence_validation_failed",
                artifact_id=None,
                created=False,
            )
        return 1


def _write_report(
    output_dir: Path,
    *,
    result: str,
    reason: str,
    artifact_id: str | None,
    created: bool,
) -> None:
    content = "\n".join(
        (
            "# Alpaca SIP entitlement admission",
            "",
            "> persisted stream evidence query only. Provider and broker access are disabled.",
            "",
            f"- result: {result}",
            f"- reason: {reason}",
            f"- artifact id: {artifact_id or 'none'}",
            f"- artifact created: {str(created).lower()}",
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
