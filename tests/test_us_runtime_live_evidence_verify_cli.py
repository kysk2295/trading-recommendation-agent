from __future__ import annotations

import stat
from pathlib import Path

import pytest

import run_us_runtime_live_evidence_verify as cli
from tests.test_us_runtime_live_evidence_verifier import _evidence


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])
    assert raised.value.code == 0


def test_missing_inputs_write_sanitized_blocked_report(tmp_path: Path) -> None:
    output = tmp_path / "report"

    code = cli.main(_arguments(tmp_path, output))

    assert code == 1
    assert not (tmp_path / "supervisor.sqlite3").exists()
    assert "result: blocked" in _report(output)
    assert "account/order mutation: 0" in _report(output)
    assert stat.S_IMODE((output / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_happy_report_contains_only_verified_aggregates(tmp_path: Path) -> None:
    request = _evidence(tmp_path)
    output = tmp_path / "report"

    code = cli.main(
        [
            "--supervisor-store",
            str(request.supervisor_store),
            "--manifest-root",
            str(request.manifest_root),
            "--receipt-root",
            str(request.receipt_root),
            "--actionability-store",
            str(request.actionability_store),
            "--output-dir",
            str(output),
        ]
    )

    content = _report(output)
    assert code == 0
    assert "result: ready" in content
    assert "completed/selected: 2/2" in content
    assert "created/replay/artifact: 1/1/1" in content
    assert "base-signal-1" not in content
    assert str(tmp_path) not in content


def _arguments(tmp_path: Path, output: Path) -> list[str]:
    return [
        "--supervisor-store",
        str(tmp_path / "supervisor.sqlite3"),
        "--manifest-root",
        str(tmp_path / "manifests"),
        "--receipt-root",
        str(tmp_path / "receipts"),
        "--actionability-store",
        str(tmp_path / "actionability.sqlite3"),
        "--output-dir",
        str(output),
    ]


def _report(output: Path) -> str:
    return (output / cli.REPORT_NAME).read_text(encoding="utf-8")
