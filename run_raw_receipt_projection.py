#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

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
_PRIVATE_OUTPUT_FILE_MODE = 0o600
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
    parent_fd, output_name = _open_existing_output_parent(output_dir)
    claimed_identity: tuple[int, int] | None = None
    try:
        os.mkdir(output_name, mode=_PRIVATE_OUTPUT_DIRECTORY_MODE, dir_fd=parent_fd)
        output_fd = _open_directory_at(parent_fd, output_name)
        try:
            claimed_identity = _directory_identity(os.fstat(output_fd))
            os.fchmod(output_fd, _PRIVATE_OUTPUT_DIRECTORY_MODE)
            os.mkdir(_STAGING_DIRECTORY_NAME, mode=_PRIVATE_OUTPUT_DIRECTORY_MODE, dir_fd=output_fd)
            staging_fd = _open_directory_at(output_fd, _STAGING_DIRECTORY_NAME)
            try:
                os.fchmod(staging_fd, _PRIVATE_OUTPUT_DIRECTORY_MODE)
                _write_private_file(staging_fd, MANIFEST_NAME, manifest_content)
                _write_private_file(staging_fd, REPORT_NAME, report_content)
                _publish_staged_output(staging_fd, output_fd)
            finally:
                os.close(staging_fd)
            os.rmdir(_STAGING_DIRECTORY_NAME, dir_fd=output_fd)
        finally:
            os.close(output_fd)
    except Exception:
        if claimed_identity is not None:
            _remove_claimed_output_directory(parent_fd, output_name, claimed_identity)
        raise
    finally:
        os.close(parent_fd)


def _open_existing_output_parent(output_dir: Path) -> tuple[int, str]:
    _require_descriptor_operations()
    destination = Path(os.path.abspath(output_dir))
    if not destination.name:
        raise OSError
    parent_fd = os.open(destination.anchor, _directory_open_flags())
    try:
        for component in destination.parts[1:-1]:
            next_fd = _open_directory_at(parent_fd, component)
            os.close(parent_fd)
            parent_fd = next_fd
        return parent_fd, destination.name
    except Exception:
        os.close(parent_fd)
        raise


def _publish_staged_output(staging_fd: int, output_fd: int) -> None:
    os.rename(
        MANIFEST_NAME,
        MANIFEST_NAME,
        src_dir_fd=staging_fd,
        dst_dir_fd=output_fd,
    )
    os.rename(
        REPORT_NAME,
        REPORT_NAME,
        src_dir_fd=staging_fd,
        dst_dir_fd=output_fd,
    )


def _remove_claimed_output_directory(
    parent_fd: int,
    output_name: str,
    claimed_identity: tuple[int, int],
) -> None:
    _assert_claimed_output_identity(parent_fd, output_name, claimed_identity)
    output_fd = _open_directory_at(parent_fd, output_name)
    try:
        if _directory_identity(os.fstat(output_fd)) != claimed_identity:
            raise OSError
        _remove_known_stage_directory(output_fd)
        _unlink_if_present(output_fd, MANIFEST_NAME)
        _unlink_if_present(output_fd, REPORT_NAME)
    finally:
        os.close(output_fd)
    _assert_claimed_output_identity(parent_fd, output_name, claimed_identity)
    os.rmdir(output_name, dir_fd=parent_fd)


def _write_private_file(directory_fd: int, name: str, content: str) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        _PRIVATE_OUTPUT_FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        os.fchmod(descriptor, _PRIVATE_OUTPUT_FILE_MODE)
        _write_all(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _remove_known_stage_directory(output_fd: int) -> None:
    try:
        staging_fd = _open_directory_at(output_fd, _STAGING_DIRECTORY_NAME)
    except FileNotFoundError:
        return
    try:
        _unlink_if_present(staging_fd, MANIFEST_NAME)
        _unlink_if_present(staging_fd, REPORT_NAME)
    finally:
        os.close(staging_fd)
    os.rmdir(_STAGING_DIRECTORY_NAME, dir_fd=output_fd)


def _unlink_if_present(directory_fd: int, name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=directory_fd)


def _assert_claimed_output_identity(
    parent_fd: int,
    output_name: str,
    claimed_identity: tuple[int, int],
) -> None:
    metadata = os.stat(output_name, dir_fd=parent_fd, follow_symlinks=False)
    if _directory_identity(metadata) != claimed_identity:
        raise OSError


def _open_directory_at(parent_fd: int, name: str) -> int:
    return os.open(name, _directory_open_flags(), dir_fd=parent_fd)


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _require_descriptor_operations() -> None:
    required = (os.open, os.mkdir, os.rename, os.rmdir, os.stat, os.unlink)
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(operation not in os.supports_dir_fd for operation in required)
        or os.stat not in os.supports_follow_symlinks
    ):
        raise OSError


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
