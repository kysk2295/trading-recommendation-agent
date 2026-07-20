from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.sec_edgar_fixture import SecEdgarFixtureError, load_sec_edgar_fixture


def test_sec_fixture_loads_relative_raw_payload(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)

    fetcher = load_sec_edgar_fixture(manifest)
    response = fetcher.fetch_submissions("sec-cycle-001", "0000320193")

    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert response.raw_payload == b"{}"


def test_sec_fixture_rejects_parent_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    directory = tmp_path / "fixture"
    directory.mkdir()
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(_manifest("../outside.json")), encoding="utf-8")

    with pytest.raises(SecEdgarFixtureError):
        _ = load_sec_edgar_fixture(manifest)


def _fixture(directory: Path) -> Path:
    directory.mkdir(exist_ok=True)
    (directory / "submissions.json").write_bytes(b"{}")
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(_manifest("submissions.json")), encoding="utf-8")
    return manifest


def _manifest(payload_path: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "received_at": "2026-07-20T14:00:00+00:00",
        "http_status": 200,
        "content_type": "application/json",
        "payload_path": payload_path,
    }
