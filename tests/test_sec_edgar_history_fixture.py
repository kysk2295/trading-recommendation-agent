from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.sec_edgar_history_fixture import (
    SecEdgarHistoryFixtureError,
    load_sec_edgar_history_fixture,
)

HISTORY = Path(__file__).parent / "fixtures/sec_edgar/additional-history-001.json"


def test_sec_history_fixture_returns_only_declared_file(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "fixture")
    fetcher = load_sec_edgar_history_fixture(manifest)

    response = fetcher.fetch_additional_history(
        "sec-history-cycle-001",
        "0000320193",
        "CIK0000320193-submissions-001.json",
    )

    assert response.raw_payload == HISTORY.read_bytes()
    assert response.received_at.isoformat() == "2026-07-20T14:01:00+00:00"


def test_sec_history_fixture_rejects_undeclared_file(tmp_path: Path) -> None:
    fetcher = load_sec_edgar_history_fixture(_manifest(tmp_path / "fixture"))

    with pytest.raises(SecEdgarHistoryFixtureError):
        _ = fetcher.fetch_additional_history(
            "sec-history-cycle-001",
            "0000320193",
            "CIK0000320193-submissions-002.json",
        )


def test_sec_history_fixture_does_not_use_unbounded_manifest_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest(tmp_path / "fixture")
    original = Path.read_bytes

    def reject_manifest_read(path: Path) -> bytes:
        if path == manifest:
            raise AssertionError("manifest must use a bounded descriptor read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_manifest_read)

    fetcher = load_sec_edgar_history_fixture(manifest)

    response = fetcher.fetch_additional_history(
        "sec-history-cycle-001",
        "0000320193",
        "CIK0000320193-submissions-001.json",
    )
    assert response.raw_payload == HISTORY.read_bytes()


def _manifest(directory: Path) -> Path:
    directory.mkdir()
    (directory / "history.json").write_bytes(HISTORY.read_bytes())
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "responses": [
                    {
                        "file_name": "CIK0000320193-submissions-001.json",
                        "received_at": "2026-07-20T14:01:00+00:00",
                        "http_status": 200,
                        "content_type": "application/json",
                        "content_encoding": "identity",
                        "payload_path": "history.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest
