from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_directed_jobs import (
    DirectedJobExecutor,
    DirectedJobRequest,
)

INTERACTION_ID = "019c0014-f0f5-7000-8000-000000000010"


@pytest.mark.parametrize(
    ("job_kind", "expected_steps"),
    [
        ("research", ("evidence_query",)),
        ("analysis", ("evidence_query", "analysis_digest")),
        ("hypothesis", ("evidence_query", "hypothesis_register")),
        ("experiment", ("evidence_query", "hypothesis_register", "experiment_run")),
        ("allowed_code", ("code_check",)),
    ],
)
def test_directed_job_runs_real_allowlisted_steps_and_emits_evidence(
    tmp_path: Path,
    job_kind: str,
    expected_steps: tuple[str, ...],
) -> None:
    # Given: a typed user-directed request and one redacted source receipt
    source = tmp_path / "source"
    source.mkdir()
    receipt = source / "receipt.json"
    receipt.write_text('{"safe_ref":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}')
    receipt.chmod(0o600)
    executor = DirectedJobExecutor(
        state_root=tmp_path / "state",
        source_evidence_root=source,
        repository=Path(__file__).resolve().parents[1],
    )
    request = DirectedJobRequest.model_validate(
        {
            "interaction_id": INTERACTION_ID,
            "agent_family_id": "systematic_quant",
            "job_kind": job_kind,
            "command": "실제 증거로 작업을 수행해줘",
        }
    )

    # When: the code-owned broker executes the bounded plan
    events = executor.execute(request)

    # Then: progress, immutable evidence and terminal result exist; prose-only is impossible
    assert tuple(event.step for event in events if event.kind == "progress") == expected_steps
    assert any(event.kind == "evidence" and event.evidence_sha256 is not None for event in events)
    assert events[-1].kind == "result"
    assert events[-1].state == "completed"
    assert events[-1].result_sha256 is not None


def test_directed_job_rejects_forbidden_tool_and_text_only_completion(tmp_path: Path) -> None:
    # Given: a request attempting provider mutation
    request = {
        "interaction_id": INTERACTION_ID,
        "agent_family_id": "day_trading",
        "job_kind": "paper_order",
        "command": "live order",
    }

    # When / Then: the typed boundary rejects it before any operation
    with pytest.raises(ValidationError):
        DirectedJobRequest.model_validate(request)
