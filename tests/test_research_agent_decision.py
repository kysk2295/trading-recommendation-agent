from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from trading_agent.research_agent_cycle_models import (
    CycleId,
    EvidenceId,
    ResearchAgentDecisionKind,
    ResearchAgentEvidenceV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_decision import (
    HermesCliResearchAgentDecisionClient,
    InvalidResearchAgentDecisionError,
    ResearchAgentDecisionParseContext,
    ResearchAgentDecisionRequest,
    parse_research_agent_decision,
    render_research_agent_prompt,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence() -> ResearchAgentEvidenceV1:
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId("a" * 64),
        agent_family_id="market_context",
        trigger_kind=ResearchAgentTriggerKind.MARKET_EVENT,
        source_key="market_context.us.current",
        evidence_refs=("b" * 64,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256="b" * 64,
        market_id="us_equities",
    )


def _request() -> ResearchAgentDecisionRequest:
    return ResearchAgentDecisionRequest(
        cycle_id=CycleId("c" * 64),
        agent_family_id="market_context",
        evidence=(_evidence(),),
        open_work=(),
        requested_at=NOW,
        max_runtime_seconds=5.0,
        max_model_calls=1,
    )


def _response() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "primary_decision": "publish_context",
            "question": "Did the current market regime change materially?",
            "summary": "The cited breadth evidence supports a bounded context update.",
            "reason": None,
            "continuation": None,
            "open_work_ref": None,
            "requested_action": "publish_context",
            "next_wake_kind": "new_evidence",
            "next_wake_at": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_decision_prompt_binds_family_memory_and_evidence() -> None:
    request = _request()

    prompt = render_research_agent_prompt(request)

    assert "<agent-family>market_context</agent-family>" in prompt
    assert "research-family:market_context:memory-v1" in prompt
    assert request.evidence[0].evidence_id in prompt
    assert "order authority: false" in prompt


def test_parser_produces_one_audited_decision() -> None:
    request = _request()
    prompt = render_research_agent_prompt(request)
    context = ResearchAgentDecisionParseContext(
        request=request,
        model_id="hermes-research-actor-v1",
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
    )

    decision = parse_research_agent_decision(_response(), context)

    assert decision.primary_decision is ResearchAgentDecisionKind.PUBLISH_CONTEXT
    assert decision.requested_action is ResearchAgentDecisionKind.PUBLISH_CONTEXT
    assert decision.next_wake_kind is ResearchAgentWakeKind.NEW_EVIDENCE
    assert decision.model_id == "hermes-research-actor-v1"
    assert decision.response_sha256 == hashlib.sha256(_response()).hexdigest()


@pytest.mark.parametrize(
    "extra",
    (
        {"secondary_decision": "publish_recommendation"},
        {"order_authority": True},
        {"argv": ["python", "strategy.py"]},
    ),
)
def test_parser_rejects_second_action_authority_or_argv(extra: dict[str, bool | str | list[str]]) -> None:
    request = _request()
    payload = json.loads(_response()) | extra
    context = ResearchAgentDecisionParseContext(
        request=request,
        model_id="hermes-research-actor-v1",
        prompt_sha256="d" * 64,
    )

    with pytest.raises(InvalidResearchAgentDecisionError):
        parse_research_agent_decision(json.dumps(payload).encode(), context)


def test_hermes_cli_client_returns_validated_decision(tmp_path: Path) -> None:
    executable = tmp_path / "hermes-fixture"
    executable.write_bytes(b"#!/bin/sh\nprintf '%s' '" + _response() + b"'\n")
    executable.chmod(0o700)
    client = HermesCliResearchAgentDecisionClient(executable, "hermes-research-actor-v1")

    decision = client.decide(_request())

    assert decision.primary_decision is ResearchAgentDecisionKind.PUBLISH_CONTEXT


@pytest.mark.parametrize("body", (b"#!/bin/sh\nexit 3\n", b"#!/bin/sh\nsleep 1\n"))
def test_hermes_cli_client_fails_closed_on_nonzero_or_timeout(tmp_path: Path, body: bytes) -> None:
    executable = tmp_path / "hermes-fixture"
    executable.write_bytes(body)
    executable.chmod(0o700)
    client = HermesCliResearchAgentDecisionClient(
        executable,
        "hermes-research-actor-v1",
        timeout_seconds=0.01,
    )

    with pytest.raises(InvalidResearchAgentDecisionError):
        client.decide(_request())
