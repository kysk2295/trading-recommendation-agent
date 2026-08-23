from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

import trading_agent.us_day_session_tick as session_module
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError
from trading_agent.us_day_session_tick import UsDaySessionTickRequest


def test_receipt_failure_downgrades_accepted_tick_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: accepted projection/tick child results followed by an immutable receipt failure.
    children = iter(
        (
            subprocess.CompletedProcess(
                ("projection",),
                0,
                json.dumps(
                    {
                        "created": "1",
                        "mutation": "0",
                        "session_id": "XNYS-2026-08-20",
                        "situation_id": "a" * 64,
                        "source": f"us_day_source_{'a' * 64}.json",
                        "status": "ready",
                    }
                ),
                "",
            ),
            subprocess.CompletedProcess(
                ("tick",),
                0,
                json.dumps({"phase": "regular", "status": "accepted"}),
                "",
            ),
        )
    )

    def run_child(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        del command
        return next(children)

    def fail_receipt(path: Path, payload: str) -> bool:
        del path, payload
        raise InvalidPrivateImmutableFileError

    monkeypatch.setattr(session_module, "_run", run_child)
    monkeypatch.setattr(session_module, "publish_private_immutable_text", fail_receipt)
    request = UsDaySessionTickRequest(
        scanner=tmp_path / "scanner.json",
        articles=tmp_path / "articles.json",
        news_evidence=tmp_path / "news.json",
        market_context=tmp_path / "context.json",
        quotes=(tmp_path / "quote.json",),
        completed_ticks=(tmp_path / "tick.json",),
        outputs=tmp_path / "outputs",
        evaluated_at=dt.datetime(2026, 8, 20, 14, 5, 40, tzinfo=dt.UTC),
    )

    # When: the production composition returns its final result.
    code, result = session_module.run_us_day_session_tick(request)

    # Then: stdout status and process status both report the durable failure.
    assert code == 2
    assert result.status == "blocked"
    assert result.reason == "session_tick_receipt_write_failed"
    assert result.receipt is None
    assert result.mutation == "0"
