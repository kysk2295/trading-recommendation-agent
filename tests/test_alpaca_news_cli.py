from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import typer

import run_alpaca_news_collect as cli


def test_fixture_happy_path_and_credential_free_replay_are_redacted(tmp_path: Path) -> None:
    database = tmp_path / "ledger" / "news.sqlite3"
    output = tmp_path / "report"
    manifest = _manifest(tmp_path / "fixture")

    _run(database, output, fixture_manifest=str(manifest))
    first = _report(output)
    _run(database, output, credentials_path=str(tmp_path / "missing.env"))
    second = _report(output)

    assert "result: success" in first
    assert "articles: 1" in first
    assert "replayed: no" in first
    assert "network access: 0" in first
    assert "replayed: yes" in second
    assert "AAPL" not in first + second
    assert "Private issuer headline" not in first + second
    assert "example.invalid" not in first + second
    assert str(tmp_path) not in first + second
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_invalid_request_blocks_before_fixture_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def reject_source(_path: Path) -> None:
        nonlocal opened
        opened = True

    monkeypatch.setattr(cli, "load_alpaca_news_fixture", reject_source)

    with pytest.raises(typer.BadParameter):
        cli.main(
            collection_id="bad id",
            symbols="AAPL",
            start_at="2026-07-21T13:00:00Z",
            end_at="2026-07-21T14:00:00Z",
            database=str(tmp_path / "news.sqlite3"),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(tmp_path / "missing.json"),
        )

    assert opened is False
    assert not (tmp_path / "news.sqlite3").exists()


def test_path_alias_and_conflicting_source_controls_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "same.sqlite3"
    with pytest.raises(typer.BadParameter):
        cli.main(
            collection_id="news-cli-alias",
            symbols="AAPL",
            start_at="2026-07-21T13:00:00Z",
            end_at="2026-07-21T14:00:00Z",
            database=str(target),
            output_dir=str(tmp_path),
            fixture_manifest=str(tmp_path / "fixture.json"),
            credentials_path=str(tmp_path / "secret.env"),
        )


def _run(
    database: Path,
    output: Path,
    *,
    fixture_manifest: str | None = None,
    credentials_path: str | None = None,
) -> None:
    cli.main(
        collection_id="news-cli-001",
        symbols="AAPL",
        start_at="2026-07-21T13:00:00Z",
        end_at="2026-07-21T14:00:00Z",
        database=str(database),
        output_dir=str(output),
        limit=50,
        max_pages=2,
        fixture_manifest=fixture_manifest,
        credentials_path=credentials_path,
    )


def _manifest(directory: Path) -> Path:
    from trading_agent.alpaca_news_models import AlpacaNewsRequest

    request = AlpacaNewsRequest.model_validate(
        {
            "collection_id": "news-cli-001",
            "symbols": ["AAPL"],
            "start_at": "2026-07-21T13:00:00Z",
            "end_at": "2026-07-21T14:00:00Z",
            "limit": 50,
            "max_pages": 2,
        }
    )
    directory.mkdir()
    payload = directory / "page.json"
    payload.write_text(
        json.dumps(
            {
                "news": [
                    {
                        "id": 1,
                        "headline": "Private issuer headline",
                        "source": "benzinga",
                        "symbols": ["AAPL"],
                        "created_at": "2026-07-21T13:30:00Z",
                        "updated_at": "2026-07-21T13:31:00Z",
                        "url": "https://example.invalid/private/1",
                    }
                ],
                "next_page_token": None,
            }
        ),
        encoding="utf-8",
    )
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": request.request_id,
                "responses": [
                    {
                        "page_index": 0,
                        "page_token": None,
                        "received_at": "2026-07-21T14:00:01Z",
                        "http_status": 200,
                        "content_type": "application/json",
                        "payload_path": "page.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _report(output: Path) -> str:
    return (output / cli.REPORT_NAME).read_text(encoding="utf-8")
