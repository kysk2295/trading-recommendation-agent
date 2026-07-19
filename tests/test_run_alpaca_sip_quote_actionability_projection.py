from __future__ import annotations

import os
from pathlib import Path

import pytest

import run_alpaca_sip_quote_actionability_projection as cli
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.test_alpaca_sip_dynamic_quote_actionability import (
    _SCAN_STARTED_AT,
    _base,
)
from tests.test_alpaca_sip_quote_actionability_projection import _receipts
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    build_alpaca_sip_quote_actionability_manifest,
    write_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore


def test_cli_projects_manifest_and_exact_replay(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    receipts = _receipts(tmp_path / "source")
    output = tmp_path / "actionability.sqlite3"
    report_dir = tmp_path / "reports"
    args = _args(manifest_path, receipts.path, output, report_dir)

    assert cli.main(args) == 0
    assert cli.main(args) == 0

    records = AlpacaSipQuoteActionabilityStore(output).records()
    assert len(records) == 1
    report = (report_dir / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: projected" in report
    assert "actionability append: replay" in report
    assert "account/order mutation: 0" in report
    assert "AAA" not in report
    assert "100.0" not in report


def test_cli_blocks_incomplete_history_without_output_store(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    receipts = _receipts(tmp_path / "source")
    output = tmp_path / "actionability.sqlite3"
    os.chmod(receipts.path, 0o644)

    assert cli.main(_args(manifest_path, receipts.path, output, tmp_path / "reports")) == 1
    assert not output.exists()
    report = (tmp_path / "reports" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: blocked" in report
    assert "actionability append: 0" in report
    assert "account/order mutation: 0" in report


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])
    assert raised.value.code == 0


def _write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    manifest = build_alpaca_sip_quote_actionability_manifest(
        _base(entry="100.10", stop="99.00"),
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    assert write_alpaca_sip_quote_actionability_manifest(path, manifest) is True
    return path


def _args(
    manifest: Path,
    receipts: Path,
    output: Path,
    report_dir: Path,
) -> list[str]:
    return [
        "--manifest",
        str(manifest),
        "--receipt-store",
        str(receipts),
        "--actionability-store",
        str(output),
        "--output-dir",
        str(report_dir),
    ]
