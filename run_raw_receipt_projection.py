#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import base64
import binascii
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.private_report import write_private_report
from trading_agent.raw_object_manifest_models import (
    RawReceipt,
    RawReceiptPayload,
    RawReceiptProjectionFixture,
)
from trading_agent.raw_receipt_projection import (
    InvalidRawReceiptProjectionError,
    project_raw_receipt_partition,
)

MANIFEST_NAME = "raw_object_partition_manifest.json"
REPORT_NAME = "raw_receipt_projection_summary.md"
_INPUT_ERROR = "raw receipt projection input is invalid"
_OUTPUT_ERROR = "raw receipt projection output could not be written"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project a synthetic local raw-receipt fixture into a content-addressed manifest."
    )
    parser.add_argument("--input", type=Path, required=True, help="synthetic receipt fixture JSON")
    parser.add_argument("--output-dir", type=Path, required=True, help="private local output directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        fixture = RawReceiptProjectionFixture.model_validate_json(args.input.read_bytes())
        receipts = tuple(
            RawReceipt(
                receipt_id=item.receipt_id,
                source_id=fixture.source_id,
                market_date=fixture.market_date,
                received_at=item.received_at,
                payload_sha256=item.payload_sha256,
                payload=RawReceiptPayload(_decode_payload(item.payload_base64)),
            )
            for item in fixture.receipts
        )
        manifest = project_raw_receipt_partition(
            receipts,
            source_id=fixture.source_id,
            market_date=fixture.market_date,
            parent_ledger_generation=fixture.parent_ledger_generation,
        )
    except (
        OSError,
        UnicodeError,
        ValidationError,
        ValueError,
        InvalidRawReceiptProjectionError,
        binascii.Error,
    ):
        print(_INPUT_ERROR, file=sys.stderr)
        return 1

    try:
        write_private_report(
            args.output_dir / MANIFEST_NAME,
            manifest.model_dump_json(indent=2) + "\n",
        )
        write_private_report(args.output_dir / REPORT_NAME, _report(manifest.receipt_count, manifest.total_byte_size))
    except OSError:
        print(_OUTPUT_ERROR, file=sys.stderr)
        return 2
    return 0


def _decode_payload(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _report(receipt_count: int, total_byte_size: int) -> str:
    return "\n".join(
        (
            "# Raw receipt projection summary",
            "",
            f"- receipt count: {receipt_count}",
            f"- total byte size: {total_byte_size}",
            "- local fixture projection only",
            "- provider, credential, broker, and collector access: none",
            "",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
