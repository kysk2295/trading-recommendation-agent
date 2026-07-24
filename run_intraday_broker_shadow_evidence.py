#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from trading_agent.intraday_broker_shadow_models import (
    InvalidBrokerShadowEvidenceError,
)
from trading_agent.intraday_broker_shadow_publication import (
    BrokerShadowPublicationRequest,
    publish_broker_shadow_evidence,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "intraday_broker_shadow_evidence_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish exact query-only Paper broker versus shadow evidence"
    )
    parser.add_argument("--current-session", type=Path, required=True)
    parser.add_argument("--execution-ledger", type=Path, required=True)
    parser.add_argument("--reviewed-at", type=_aware_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output_dir.resolve(strict=False)
    try:
        artifact, created = publish_broker_shadow_evidence(
            BrokerShadowPublicationRequest(
                args.current_session.resolve(strict=False),
                args.execution_ledger.resolve(strict=False),
                output,
                args.reviewed_at,
            )
        )
    except (InvalidBrokerShadowEvidenceError, OSError, TypeError, ValueError):
        write_private_report(
            output / REPORT_NAME,
            "# Intraday Paper broker/shadow evidence\n\n"
            "- result: blocked\n"
            "- automatic state change: false\n"
            "- order authority change: false\n"
            "- allocation change: false\n"
            "- external mutation: 0\n",
        )
        return 1
    payload = artifact.payload
    write_private_report(
        output / REPORT_NAME,
        "# Intraday Paper broker/shadow evidence\n\n"
        f"- result: {payload.status.value}\n"
        f"- artifact id: {artifact.artifact_id}\n"
        f"- execution snapshot sha256: {payload.execution_snapshot_sha256}\n"
        f"- shadow source sha256: {payload.shadow_source_sha256}\n"
        f"- paired sessions: {payload.paired_session_count}\n"
        f"- paired trades: {payload.paired_trade_count}\n"
        f"- unpaired broker intents: {payload.unpaired_broker_intent_count}\n"
        f"- blockers: {', '.join(payload.blockers) or 'none'}\n"
        f"- created: {str(created).lower()}\n"
        "- automatic state change: false\n"
        "- order authority change: false\n"
        "- allocation change: false\n"
        "- external mutation: 0\n",
    )
    return 0


def _aware_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "reviewed-at must be ISO-8601"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("reviewed-at must include timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
