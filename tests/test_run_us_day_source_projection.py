from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic import TypeAdapter

import run_us_day_source_projection as cli
from tests.test_us_day_situation_projection import _inputs
from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_source_models import CanonicalUsDaySource
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle


def test_source_projection_cli_publishes_one_private_canonical_source(tmp_path: Path) -> None:
    arguments = _fixture_arguments(tmp_path)

    code = cli.main(arguments)

    assert code == 0
    sources = tuple((tmp_path / "sources").glob("us_day_source_*.json"))
    assert len(sources) == 1
    source = CanonicalUsDaySource.model_validate_json(sources[0].read_text(encoding="utf-8"))
    assert source.situation.session_id == "XNYS-2026-08-20"
    assert tuple(item.symbol for item in source.current_markets) == ("AMD", "NVDA")
    assert sources[0].stat().st_mode & 0o777 == 0o600


def test_source_projection_cli_blocks_incomplete_market_inputs(tmp_path: Path) -> None:
    arguments = _fixture_arguments(tmp_path)
    quote_index = arguments.index("--quote")
    del arguments[quote_index : quote_index + 2]

    code = cli.main(arguments)

    assert code == 2
    assert not tuple((tmp_path / "sources").glob("us_day_source_*.json"))


def test_source_projection_cli_help_is_available() -> None:
    try:
        _ = cli.parse_args(["--help"])
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("help did not exit")


def test_source_projection_cli_imports_no_broker_or_execution_authority() -> None:
    script = (
        "import json,sys; import run_us_day_source_projection; "
        "print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))"
    )

    completed = subprocess.run(
        (sys.executable, "-c", script),
        check=True,
        capture_output=True,
        text=True,
    )

    loaded = json.loads(completed.stdout)
    forbidden = ("broker", "credential", "execution", "paper_execution", "alpaca_paper")
    assert not {name for name in loaded if any(marker in name for marker in forbidden)}


def _fixture_arguments(tmp_path: Path) -> list[str]:
    inputs = _inputs()
    scanner = _private_json(
        tmp_path / "scanner.json",
        TypeAdapter(UsOpportunityScannerBundle).dump_json(inputs.scanner).decode(),
    )
    articles = _private_json(
        tmp_path / "articles.json",
        TypeAdapter(tuple[AlpacaNewsArticle, ...]).dump_json(inputs.articles).decode(),
    )
    news = _private_json(tmp_path / "news.json", inputs.news_evidence.model_dump_json())
    context = _private_json(tmp_path / "context.json", inputs.market_context.model_dump_json())
    quote_paths = tuple(
        _private_json(tmp_path / f"quote-{item.symbol}.json", item.model_dump_json())
        for item in inputs.quotes
    )
    tick_paths = tuple(
        _private_json(tmp_path / f"tick-{item.bars[-1].symbol}.json", item.model_dump_json())
        for item in inputs.completed_bars
    )
    return [
        "--scanner",
        str(scanner),
        "--articles",
        str(articles),
        "--news-evidence",
        str(news),
        "--market-context",
        str(context),
        *(value for path in quote_paths for value in ("--quote", str(path))),
        *(value for path in tick_paths for value in ("--completed-tick", str(path))),
        "--output-root",
        str(tmp_path / "sources"),
        "--now",
        inputs.evaluated_at.isoformat(),
    ]


def _private_json(path: Path, payload: str) -> Path:
    _ = publish_private_immutable_text(path, payload + ("" if payload.endswith("\n") else "\n"))
    return path
