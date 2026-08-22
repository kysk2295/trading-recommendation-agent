from __future__ import annotations

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.private_immutable_file import publish_private_immutable_text, read_private_text
from trading_agent.us_day_source_projection import project_us_day_source
from trading_agent.us_day_thesis_models import situation_id_for
from trading_agent.us_forward_shadow_models import UsForwardShadowTick
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence

_SCANNER = TypeAdapter(UsOpportunityScannerBundle)
_ARTICLES = TypeAdapter(tuple[AlpacaNewsArticle, ...])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project verified current US session evidence into one immutable Day Agent source."
    )
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--articles", type=Path, required=True)
    parser.add_argument("--news-evidence", type=Path, required=True)
    parser.add_argument("--market-context", type=Path, required=True)
    parser.add_argument("--quote", type=Path, action="append", required=True)
    parser.add_argument("--completed-tick", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--now")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        evaluated_at = _now(args.now)
        scanner = _SCANNER.validate_json(read_private_text(args.scanner))
        articles = _ARTICLES.validate_json(read_private_text(args.articles))
        news = AlpacaNewsOpportunityEvidenceBundle.model_validate_json(
            read_private_text(args.news_evidence)
        )
        context = MarketContextSnapshot.model_validate_json(read_private_text(args.market_context))
        quotes = tuple(
            UsQuotePolicyEvidence.model_validate_json(read_private_text(path)) for path in args.quote
        )
        ticks = tuple(
            UsForwardShadowTick.model_validate_json(read_private_text(path))
            for path in args.completed_tick
        )
        source = project_us_day_source(
            scanner=scanner,
            articles=articles,
            news_evidence=news,
            market_context=context,
            quotes=quotes,
            completed_bars=ticks,
            evaluated_at=evaluated_at,
        )
        situation_id = situation_id_for(source.situation)
        target = args.output_root.expanduser().absolute() / f"us_day_source_{situation_id}.json"
        created = publish_private_immutable_text(
            target,
            canonical_experiment_ledger_json(source) + "\n",
        )
    except (OSError, TypeError, ValidationError, ValueError):
        _emit({"mutation": "0", "reason": "source_projection_blocked", "status": "blocked"})
        return 2
    _emit(
        {
            "created": str(int(created)),
            "mutation": "0",
            "session_id": source.situation.session_id,
            "situation_id": situation_id,
            "source": target.name,
            "status": "ready",
        }
    )
    return 0


def _now(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.UTC)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(dt.UTC)


def _emit(payload: dict[str, str]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
