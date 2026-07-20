from __future__ import annotations

import ast
import datetime as dt
import json
import stat
from pathlib import Path

import pytest
import typer

import run_alpaca_news_opportunity_evidence as cli
from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_coverage_models import AlpacaNewsCoverageManifest
from trading_agent.alpaca_news_models import AlpacaNewsRawResponse, AlpacaNewsRequest
from trading_agent.alpaca_news_store import AlpacaNewsStore

START = dt.datetime(2026, 7, 20, 16, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED = END + dt.timedelta(seconds=1)
COMPLETED = END + dt.timedelta(seconds=2)
CUTOFF = END + dt.timedelta(seconds=3)


class _Fetcher:
    def __init__(self, symbol: str, *, has_article: bool) -> None:
        self.symbol = symbol
        self.has_article = has_article

    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        return AlpacaNewsRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=RECEIVED,
            status_code=200,
            content_type="application/json",
            raw_payload=_payload(self.symbol, self.has_article),
        )


def test_complete_cli_publishes_redacted_coverage_and_evidence_once(tmp_path: Path) -> None:
    database, manifest = _fixture(tmp_path, complete=True)
    output = tmp_path / "output"

    cli.main(manifest=str(manifest), database=str(database), output_dir=str(output))
    first = _report(output)
    cli.main(manifest=str(manifest), database=str(database), output_dir=str(output))
    second = _report(output)

    assert "result: complete" in first
    assert "successful symbols: 2/2" in first
    assert "coverage artifact created: 1" in first
    assert "evidence artifact created: 1" in first
    assert "coverage artifact created: 0" in second
    assert "evidence artifact created: 0" in second
    assert "network access: 0" in second
    assert "AAPL" not in first + second
    assert "Synthetic private headline" not in first + second
    assert str(tmp_path) not in first + second
    assert len(tuple(output.glob("alpaca_news_coverage_*.json"))) == 1
    assert len(tuple(output.glob("alpaca_news_opportunity_evidence_*.json"))) == 1
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.glob("*.json"))
    assert stat.S_IMODE((output / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_incomplete_cli_persists_assessment_without_evidence_and_exits_two(
    tmp_path: Path,
) -> None:
    database, manifest = _fixture(tmp_path, complete=False)
    output = tmp_path / "output"

    with pytest.raises(typer.Exit) as raised:
        cli.main(manifest=str(manifest), database=str(database), output_dir=str(output))

    report = _report(output)
    assert raised.value.exit_code == 2
    assert "result: incomplete" in report
    assert "successful symbols: 1/2" in report
    assert "missing slices: 1" in report
    assert "coverage artifact created: 1" in report
    assert "evidence artifact created: 0" in report
    assert len(tuple(output.glob("alpaca_news_coverage_*.json"))) == 1
    assert not tuple(output.glob("alpaca_news_opportunity_evidence_*.json"))


def test_cli_is_query_only_and_has_no_provider_or_execution_imports() -> None:
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "trading_agent.alpaca_news_client" not in modules
    assert "trading_agent.alpaca_http" not in modules
    assert "trading_agent.alpaca_private_credentials" not in modules
    assert "trading_agent.paper_execution" not in modules


def _fixture(tmp_path: Path, *, complete: bool) -> tuple[Path, Path]:
    first = _request("news-cli-evidence-a", "AAPL")
    second = _request("news-cli-evidence-b", "MSFT")
    database = tmp_path / "ledger" / "news.sqlite3"
    store = AlpacaNewsStore(database)
    _collect(store, first, "AAPL", has_article=True)
    if complete:
        _collect(store, second, "MSFT", has_article=False)
    manifest = AlpacaNewsCoverageManifest(
        universe_id="us_news_cli_fixture",
        cutoff_at=CUTOFF,
        requests=(first, second),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    manifest_path.chmod(0o600)
    return database, manifest_path


def _request(collection_id: str, symbol: str) -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id=collection_id,
        symbols=(symbol,),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=2,
    )


def _collect(
    store: AlpacaNewsStore,
    request: AlpacaNewsRequest,
    symbol: str,
    *,
    has_article: bool,
) -> None:
    _ = collect_alpaca_news(
        _Fetcher(symbol, has_article=has_article),
        store,
        request,
        _clock=lambda: COMPLETED,
    )


def _payload(symbol: str, has_article: bool) -> bytes:
    news = (
        [
            {
                "id": 1,
                "headline": "Synthetic private headline",
                "source": "benzinga",
                "symbols": [symbol],
                "created_at": "2026-07-20T16:30:00Z",
                "updated_at": "2026-07-20T16:31:00Z",
                "url": "https://example.invalid/private/1",
            }
        ]
        if has_article
        else []
    )
    return json.dumps({"news": news, "next_page_token": None}).encode()


def _report(output: Path) -> str:
    return (output / cli.REPORT_NAME).read_text(encoding="utf-8")
