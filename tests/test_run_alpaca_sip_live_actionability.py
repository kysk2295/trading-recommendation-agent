from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

import run_alpaca_sip_live_actionability as cli
from tests.alpaca_sip_dynamic_reconnect_fixtures import ConnectorQueue, FixtureClock
from tests.test_alpaca_sip_live_actionability import (
    _CAPTURE_AT,
    _EPOCH,
    _connection,
    _dependencies,
    _request,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    write_alpaca_sip_quote_actionability_manifest,
)


def test_help_exposes_explicit_read_only_arm() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    assert raised.value.code == 0


def test_missing_arm_blocks_before_clock_credentials_or_state(tmp_path: Path) -> None:
    def forbidden_clock() -> dt.datetime:
        raise AssertionError

    code = cli.main(
        _args(tmp_path),
        dependencies=cli.default_dependencies(clock=forbidden_clock),
    )

    assert code == 1
    assert not (tmp_path / "receipts.sqlite3").exists()
    assert not (tmp_path / "actionability.sqlite3").exists()


def test_closed_session_blocks_before_credentials_or_state(tmp_path: Path) -> None:
    closed = dt.datetime(2026, 7, 19, 14, 35, tzinfo=dt.UTC)
    queue = ConnectorQueue([_connection()])

    code = cli.main(
        [*_args(tmp_path), "--arm-read-only"],
        dependencies=_dependencies(queue, FixtureClock(closed), (_EPOCH,)),
    )

    assert code == 1
    assert queue.calls == 0
    assert not (tmp_path / "receipts.sqlite3").exists()


def test_cli_captures_projects_and_replays_without_second_connection(tmp_path: Path) -> None:
    request = _request(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    assert write_alpaca_sip_quote_actionability_manifest(manifest_path, request.manifest)
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    first_queue = ConnectorQueue([_connection()])
    arguments = [*_args(tmp_path, manifest=manifest_path, secret=secret), "--arm-read-only"]

    first = cli.main(
        arguments,
        dependencies=_dependencies(first_queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )
    replay_queue = ConnectorQueue([_connection()])
    replay = cli.main(
        arguments,
        dependencies=_dependencies(
            replay_queue,
            FixtureClock(_CAPTURE_AT + dt.timedelta(seconds=1)),
            ("2" * 32,),
        ),
    )

    assert first == 0
    assert replay == 0
    assert first_queue.calls == 1
    assert replay_queue.calls == 0
    report = (tmp_path / "reports" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: projected" in report
    assert "actionability append: replay" in report
    assert "account/order mutation: 0" in report
    assert "AAA" not in report
    assert "100.0" not in report


def test_public_credential_file_blocks_before_connector(tmp_path: Path) -> None:
    request = _request(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    assert write_alpaca_sip_quote_actionability_manifest(manifest_path, request.manifest)
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n",
        encoding="utf-8",
    )
    secret.chmod(0o640)
    queue = ConnectorQueue([_connection()])

    code = cli.main(
        [*_args(tmp_path, manifest=manifest_path, secret=secret), "--arm-read-only"],
        dependencies=_dependencies(queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )

    assert code == 1
    assert queue.calls == 0
    assert not (tmp_path / "receipts.sqlite3").exists()


def _args(
    tmp_path: Path,
    *,
    manifest: Path | None = None,
    secret: Path | None = None,
) -> list[str]:
    return [
        "--manifest",
        str(tmp_path / "missing-manifest.json" if manifest is None else manifest),
        "--plan-store",
        str(tmp_path / "plans.sqlite3"),
        "--policy-state-store",
        str(tmp_path / "policy.sqlite3"),
        "--receipt-store",
        str(tmp_path / "receipts.sqlite3"),
        "--actionability-store",
        str(tmp_path / "actionability.sqlite3"),
        "--output-dir",
        str(tmp_path / "reports"),
        "--secret-path",
        str(tmp_path / "missing.env" if secret is None else secret),
        "--max-data-frames",
        "1",
    ]
