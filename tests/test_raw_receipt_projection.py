from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from trading_agent.raw_object_manifest_models import RawReceipt, RawReceiptPayload
from trading_agent.raw_receipt_projection import (
    InvalidRawReceiptProjectionError,
    project_raw_receipt_partition,
)

MARKET_DATE = dt.date(2026, 7, 17)
RECEIVED_AT = dt.datetime(2026, 7, 17, 9, 30, tzinfo=dt.UTC)
PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_raw_receipt_projection.py"
CLI_PAYLOAD = b"synthetic-cli-private-payload"


def _receipt(
    receipt_id: str,
    payload: bytes,
    received_at: dt.datetime,
    *,
    source_id: str = "synthetic.market",
    market_date: dt.date = MARKET_DATE,
) -> RawReceipt:
    return RawReceipt(
        receipt_id=receipt_id,
        source_id=source_id,
        market_date=market_date,
        received_at=received_at,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload=RawReceiptPayload(payload),
    )


def test_projection_replays_deterministically_without_mutating_receipts() -> None:
    receipts = (
        _receipt("a" * 64, b"one", RECEIVED_AT),
        _receipt("b" * 64, b"one", RECEIVED_AT + dt.timedelta(seconds=1)),
    )

    first = project_raw_receipt_partition(
        receipts,
        source_id="synthetic.market",
        market_date=MARKET_DATE,
        parent_ledger_generation=7,
    )
    second = project_raw_receipt_partition(
        receipts,
        source_id="synthetic.market",
        market_date=MARKET_DATE,
        parent_ledger_generation=7,
    )

    assert first == second
    assert first.manifest_id == first.content_sha256
    assert first.receipt_count == 2
    assert first.total_byte_size == len(b"one") * 2
    assert first.receipts[0].receipt_id == "a" * 64
    assert first.receipts[1].receipt_id == "b" * 64
    assert receipts[0].payload.value == b"one"
    assert "b'one'" not in repr(first)


@pytest.mark.parametrize(
    ("changed_receipts", "source_id", "market_date", "parent_generation"),
    (
        (
            (_receipt("a" * 64, b"changed", RECEIVED_AT),),
            "synthetic.market",
            MARKET_DATE,
            7,
        ),
        (
            (_receipt("b" * 64, b"one", RECEIVED_AT),),
            "synthetic.market",
            MARKET_DATE,
            7,
        ),
        (
            (_receipt("a" * 64, b"one", RECEIVED_AT + dt.timedelta(seconds=1)),),
            "synthetic.market",
            MARKET_DATE,
            7,
        ),
        (
            (_receipt("a" * 64, b"one", RECEIVED_AT, source_id="alternate.market"),),
            "alternate.market",
            MARKET_DATE,
            7,
        ),
        (
            (
                _receipt(
                    "a" * 64,
                    b"one",
                    RECEIVED_AT,
                    market_date=MARKET_DATE + dt.timedelta(days=1),
                ),
            ),
            "synthetic.market",
            MARKET_DATE + dt.timedelta(days=1),
            7,
        ),
        (
            (_receipt("a" * 64, b"one", RECEIVED_AT),),
            "synthetic.market",
            MARKET_DATE,
            8,
        ),
    ),
)
def test_projection_changes_identity_for_each_partition_input_change(
    changed_receipts: tuple[RawReceipt, ...],
    source_id: str,
    market_date: dt.date,
    parent_generation: int,
) -> None:
    baseline = project_raw_receipt_partition(
        (_receipt("a" * 64, b"one", RECEIVED_AT),),
        source_id="synthetic.market",
        market_date=MARKET_DATE,
        parent_ledger_generation=7,
    )

    changed = project_raw_receipt_partition(
        changed_receipts,
        source_id=source_id,
        market_date=market_date,
        parent_ledger_generation=parent_generation,
    )

    assert changed.manifest_id != baseline.manifest_id


def test_projection_rejects_mutated_payload_mixed_partition_and_noncanonical_order() -> None:
    tampered = _receipt("a" * 64, b"one", RECEIVED_AT)
    object.__setattr__(tampered.payload, "value", b"tampered")
    mixed_source = _receipt(
        "b" * 64,
        b"two",
        RECEIVED_AT + dt.timedelta(seconds=1),
        source_id="other.market",
    )
    mixed_market_date = _receipt(
        "b" * 64,
        b"two",
        RECEIVED_AT,
        market_date=MARKET_DATE + dt.timedelta(days=1),
    )

    for receipts in (
        (tampered,),
        (_receipt("a" * 64, b"one", RECEIVED_AT), mixed_source),
        (_receipt("a" * 64, b"one", RECEIVED_AT), mixed_market_date),
        (_receipt("b" * 64, b"two", RECEIVED_AT), _receipt("a" * 64, b"one", RECEIVED_AT)),
        (_receipt("a" * 64, b"one", RECEIVED_AT), _receipt("a" * 64, b"two", RECEIVED_AT)),
        (),
    ):
        with pytest.raises(InvalidRawReceiptProjectionError, match="raw receipt partition"):
            _ = project_raw_receipt_partition(
                receipts,
                source_id="synthetic.market",
                market_date=MARKET_DATE,
                parent_ledger_generation=7,
            )

    with pytest.raises(InvalidRawReceiptProjectionError, match="raw receipt partition"):
        _ = project_raw_receipt_partition(
            (_receipt("a" * 64, b"one", RECEIVED_AT),),
            source_id="synthetic.market",
            market_date=MARKET_DATE,
            parent_ledger_generation=-1,
        )


def test_projection_cli_help_is_local_and_fixture_only() -> None:
    completed = _run_cli("--help")

    assert completed.returncode == 0, completed.stderr
    assert "--input" in completed.stdout
    assert "--output-dir" in completed.stdout
    assert "credential" not in completed.stdout.lower()
    assert "endpoint" not in completed.stdout.lower()
    assert "payload-path" not in completed.stdout.lower()


def test_projection_cli_bad_input_is_sanitized(tmp_path: Path) -> None:
    fixture = tmp_path / "bad-fixture.json"
    fixture.write_text('{"receipt":"' + CLI_PAYLOAD.decode() + '"}', encoding="utf-8")

    completed = _run_cli("--input", str(fixture), "--output-dir", str(tmp_path / "output"))

    assert completed.returncode == 1
    assert "raw receipt projection input is invalid" in completed.stderr
    assert CLI_PAYLOAD.decode() not in completed.stderr
    assert not (tmp_path / "output").exists()


def test_projection_cli_writes_only_private_aggregate_report_and_manifest(tmp_path: Path) -> None:
    fixture = tmp_path / "synthetic-fixture.json"
    fixture.write_text(json.dumps(_fixture()), encoding="utf-8")
    output = tmp_path / "output"

    completed = _run_cli("--input", str(fixture), "--output-dir", str(output))

    assert completed.returncode == 0, completed.stderr
    paths = tuple(sorted(output.iterdir()))
    assert tuple(path.name for path in paths) == (
        "raw_object_partition_manifest.json",
        "raw_receipt_projection_summary.md",
    )
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)
    manifest = json.loads((output / "raw_object_partition_manifest.json").read_text(encoding="utf-8"))
    report = (output / "raw_receipt_projection_summary.md").read_text(encoding="utf-8")
    assert manifest["receipt_count"] == 1
    assert manifest["receipts"][0]["receipt_id"] == "a" * 64
    assert "payload_base64" not in json.dumps(manifest)
    assert CLI_PAYLOAD.decode() not in json.dumps(manifest)
    assert CLI_PAYLOAD.decode() not in report
    assert hashlib.sha256(CLI_PAYLOAD).hexdigest() not in report
    assert "credential" not in completed.stdout.lower()
    assert "endpoint" not in completed.stdout.lower()


def _fixture() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_id": "synthetic.market",
        "market_date": MARKET_DATE.isoformat(),
        "parent_ledger_generation": 3,
        "receipts": [
            {
                "receipt_id": "a" * 64,
                "received_at": "2026-07-17T09:30:00Z",
                "payload_sha256": hashlib.sha256(CLI_PAYLOAD).hexdigest(),
                "payload_base64": base64.b64encode(CLI_PAYLOAD).decode("ascii"),
            }
        ],
    }


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["UV_NO_SYNC"] = "1"
    return subprocess.run(
        (str(SCRIPT), *args),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
