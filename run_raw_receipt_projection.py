#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
import shutil
import stat
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
_FIXTURE_SOURCE_ID = re.compile(r"^fixture\.[a-z0-9][a-z0-9_.-]{0,55}$")
_PRIVATE_OUTPUT_DIRECTORY_MODE = 0o700
_STAGING_DIRECTORY_NAME = ".staging"


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
        fixture, receipts = load_raw_receipt_projection_fixture(args.input)
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
        _write_projection_output(
            args.output_dir,
            manifest.model_dump_json(indent=2) + "\n",
            _report(manifest.receipt_count, manifest.total_byte_size),
        )
    except OSError:
        print(_OUTPUT_ERROR, file=sys.stderr)
        return 2
    return 0


def _write_projection_output(output_dir: Path, manifest_content: str, report_content: str) -> None:
    destination = _validate_output_target(output_dir)
    claimed = False
    try:
        destination.mkdir(mode=_PRIVATE_OUTPUT_DIRECTORY_MODE)
        claimed = True
        destination.chmod(_PRIVATE_OUTPUT_DIRECTORY_MODE)
        staging = destination / _STAGING_DIRECTORY_NAME
        staging.mkdir(mode=_PRIVATE_OUTPUT_DIRECTORY_MODE)
        staging.chmod(_PRIVATE_OUTPUT_DIRECTORY_MODE)
        write_private_report(staging / MANIFEST_NAME, manifest_content)
        write_private_report(staging / REPORT_NAME, report_content)
        _publish_staged_output(staging, destination)
    except Exception:
        if claimed:
            _remove_claimed_output_directory(destination)
        raise


def _validate_output_target(output_dir: Path) -> Path:
    destination = Path(os.path.abspath(output_dir))
    current = Path(destination.anchor)
    for component in destination.parts[1:-1]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError:
            raise OSError from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
    try:
        _ = destination.lstat()
    except FileNotFoundError:
        return destination
    except OSError:
        raise OSError from None
    raise OSError


def _publish_staged_output(staging: Path, destination: Path) -> None:
    (staging / MANIFEST_NAME).replace(destination / MANIFEST_NAME)
    (staging / REPORT_NAME).replace(destination / REPORT_NAME)
    staging.rmdir()


def _remove_claimed_output_directory(destination: Path) -> None:
    shutil.rmtree(destination)


def load_raw_receipt_projection_fixture(
    path: Path,
) -> tuple[RawReceiptProjectionFixture, tuple[RawReceipt, ...]]:
    fixture = RawReceiptProjectionFixture.model_validate_json(path.read_bytes())
    if _FIXTURE_SOURCE_ID.fullmatch(fixture.source_id) is None:
        raise ValueError("invalid raw receipt projection fixture")
    receipts = tuple(
        RawReceipt.from_payload(
            receipt_id=item.receipt_id,
            source_id=fixture.source_id,
            market_date=fixture.market_date,
            received_at=item.received_at,
            payload_sha256=item.payload_sha256,
            payload=RawReceiptPayload(_decode_payload(item.payload_base64)),
        )
        for item in fixture.receipts
    )
    return fixture, receipts


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
