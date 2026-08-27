from __future__ import annotations

import json
from typing import Final

from trading_agent.day_sensitive_content import contains_sensitive_text
from trading_agent.researcher_agent import ResearcherContext
from trading_agent.researcher_llm_contracts import LlmHypothesisDraft, ResearcherLlmError

_MAX_FREE_PARAMETERS: Final = 4

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


def _prompt(context: ResearcherContext) -> str:
    payload = _prompt_payload(context)
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if context.bounded_day_discovery_json is not None and contains_sensitive_text((prompt,)):
        raise ResearcherLlmError
    return prompt


def _prompt_payload(context: ResearcherContext) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "contract": _prompt_contract(),
        "existing_hypothesis_texts": list(context.existing_hypothesis_texts),
        "failure_digest": {
            "censored_reasons": list(context.failure_digest.censored_reasons),
            "failed_falsifications": list(context.failure_digest.failed_falsifications),
            "rejected_hypothesis_texts": list(context.failure_digest.rejected_hypothesis_texts),
            "reviewer_decisions": list(context.failure_digest.reviewer_decisions),
        },
        "lane_id": context.lane_id.value,
        "regime_context": context.regime_context,
        "sources": [
            {
                "claim": source.claim,
                "limitations": source.limitations,
                "source_id": source.source_id,
                "source_kind": source.source_kind.value,
                "title": source.title,
            }
            for source in context.sources
        ],
    }
    if context.bounded_day_discovery_json is not None:
        try:
            bounded = json.loads(context.bounded_day_discovery_json)
        except (TypeError, ValueError):
            raise ResearcherLlmError from None
        if not isinstance(bounded, dict) or len(context.bounded_day_discovery_json.encode()) > 48 * 1024:
            raise ResearcherLlmError
        payload["day_discovery"] = bounded
    return payload


def _prompt_contract() -> dict[str, JsonValue]:
    return {
        "counterfactual_baseline": "existing_approved_strategy",
        "economic_mechanism": "derive_only_from_cited_source_claims",
        "falsification_rule": "specific_measurable_thresholds",
        "maximum_free_parameters": _MAX_FREE_PARAMETERS,
        "only_raw_json": True,
        "output_json_schema": LlmHypothesisDraft.model_json_schema(),
        "strategy_entrypoint": {
            "factory": "create_strategy(context)",
            "method": "observe(bar, candidate)",
        },
        "strategy_source_contract": {
            "content": "complete_syntactically_valid_python_source_only",
            "factory": "define create_strategy(context) returning a stateful object with observe",
            "no_markdown_or_prose": True,
            "observe_inputs": {
                "bar_keys": [
                    "symbol",
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "prior_close",
                    "average_daily_volume",
                    "spread_bps",
                    "catalyst",
                ],
                "candidate": "null or a dict with symbol,timestamp,price,gap_pct,change_pct,"
                "relative_volume,cumulative_dollar_volume,spread_bps,catalyst",
            },
            "observe_return": {
                "no_signal": None,
                "signal_constraints": "echo bar symbol and timestamp; finite entry greater than stop",
                "signal_exact_keys": ["symbol", "timestamp", "entry", "stop", "rationale"],
            },
        },
    }


__all__ = ()
