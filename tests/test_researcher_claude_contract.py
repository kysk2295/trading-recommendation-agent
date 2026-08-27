from __future__ import annotations

import json
from pathlib import Path

from trading_agent import autonomous_reasoning_codec, researcher_llm
from trading_agent.researcher_llm import HermesCliProposalClient, LlmHypothesisDraft


def test_claude_schema_falls_back_for_non_json_prompt() -> None:
    assert researcher_llm._claude_response_schema("not-json") == (LlmHypothesisDraft.model_json_schema())


def test_claude_client_unwraps_discriminated_response_envelope(tmp_path: Path) -> None:
    executable = tmp_path / "claude-envelope-fixture"
    executable.write_text(
        "#!/bin/sh\n"
        'printf \'%s\' \'{"is_error":false,"structured_output":{"response":'
        '{"kind":"defer","next_wake_event":"market_open",'
        '"reason":"Waiting for market open.","resume_condition":'
        '"Resume when the market opens."}}}\'\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    prompt = json.dumps(
        {
            "response_schema": {
                "$defs": {
                    "Defer": {
                        "properties": {"kind": {"const": "defer"}},
                        "required": ["kind"],
                        "type": "object",
                    }
                },
                "discriminator": {"propertyName": "kind"},
                "oneOf": [{"$ref": "#/$defs/Defer"}],
            }
        }
    )

    response = HermesCliProposalClient(executable, "haiku", "claude-code").complete(prompt)

    assert json.loads(response) == {
        "kind": "defer",
        "next_wake_event": "market_open",
        "reason": "Waiting for market open.",
        "resume_condition": "Resume when the market opens.",
    }


def test_autonomous_defer_schema_requires_exactly_one_non_null_wake() -> None:
    schema = autonomous_reasoning_codec._autonomous_response_schema()
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    defer = definitions["AutonomousDefer"]
    assert isinstance(defer, dict)

    assert defer["oneOf"] == [
        {
            "properties": {
                "next_wake_at": {"format": "date-time", "type": "string"},
                "next_wake_event": {"type": "null"},
            },
            "required": ["next_wake_at", "next_wake_event"],
        },
        {
            "properties": {
                "next_wake_at": {"type": "null"},
                "next_wake_event": {"maxLength": 160, "minLength": 1, "type": "string"},
            },
            "required": ["next_wake_at", "next_wake_event"],
        },
    ]
