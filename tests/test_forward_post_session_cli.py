from __future__ import annotations

import stat
from pathlib import Path

import pytest
import typer

import run_forward_post_session as cli
from trading_agent.forward_post_session import (
    ForwardPostSessionError,
    ForwardPostSessionResult,
    ForwardPostSessionStatus,
)


def test_cli_writes_private_recovered_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = tmp_path / "session"
    session.mkdir()
    output = tmp_path / "report"
    result = ForwardPostSessionResult(
        ForwardPostSessionStatus.RECOVERED,
        390,
        390,
        390,
        390,
        3_200,
        12_480,
        32,
        0,
        10,
    )
    monkeypatch.setattr(
        cli,
        "close_forward_post_session",
        lambda *_args, **_kwargs: result,
    )

    cli.main(
        session,
        session_date="2026-07-23",
        minimum_watch_cycles=300,
        output_dir=output,
    )

    report = output / cli.REPORT_NAME
    content = report.read_text(encoding="utf-8")
    assert "- result: recovered" in content
    assert "- watch cycles: 390" in content
    assert "- candidate inputs: 3200" in content
    assert "- quality gate relaxed: false" in content
    assert "- provider, credential, account, or order operation: 0" in content
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


def test_cli_preserves_blocker_in_private_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = tmp_path / "session"
    session.mkdir()
    output = tmp_path / "report"

    def blocked(*_args: object, **_kwargs: object) -> ForwardPostSessionResult:
        raise ForwardPostSessionError("post_session_failure_preserved")

    monkeypatch.setattr(cli, "close_forward_post_session", blocked)

    with pytest.raises(typer.Exit) as caught:
        cli.main(
            session,
            session_date="2026-07-23",
            minimum_watch_cycles=300,
            output_dir=output,
        )

    assert caught.value.exit_code == 1
    content = (output / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "- result: blocked" in content
    assert "- reason: post_session_failure_preserved" in content
    assert "- failed cycle deletion: 0" in content


def test_cli_rejects_bad_date_before_output(tmp_path: Path) -> None:
    output = tmp_path / "report"

    with pytest.raises(typer.BadParameter):
        cli.main(
            tmp_path,
            session_date="not-a-date",
            output_dir=output,
        )

    assert not output.exists()
