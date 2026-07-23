from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.intraday_research_input_binding_fixtures import NOW
from tests.test_intraday_actual_research import _request
from tests.test_intraday_actual_research_plan import _spec
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
)
from trading_agent.intraday_actual_research_audit import (
    audit_intraday_actual_research,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditError,
    IntradayActualResearchAuditRequest,
)
from trading_agent.intraday_actual_research_plan import (
    run_planned_intraday_actual_research,
)
from trading_agent.intraday_research_reviewer import IntradayReviewArtifact
from trading_agent.private_report import write_private_report


def test_actual_research_audit_recomputes_independent_review(
    tmp_path: Path,
) -> None:
    request, _ = _request(tmp_path)
    planned = run_planned_intraday_actual_research(
        _spec(request, run_key="actual-2026-07-14"),
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW,
    )
    review_path = next(request.paths.review_root.glob("*.json"))
    review = IntradayReviewArtifact.model_validate_json(
        review_path.read_text(encoding="utf-8")
    )
    stale_evidence = review.payload.evidence.model_copy(
        update={
            "observed_sessions": (
                review.payload.evidence.observed_sessions + 1
            )
        }
    )
    stale_payload = review.payload.model_copy(
        update={"evidence": stale_evidence}
    )
    stale_id = hashlib.sha256(
        canonical_experiment_ledger_json(stale_payload).encode()
    ).hexdigest()
    stale_review = IntradayReviewArtifact(
        artifact_id=stale_id,
        payload=stale_payload,
    )
    review_path.unlink()
    write_private_report(
        review_path.with_name(f"intraday_research_review_{stale_id}.json"),
        canonical_experiment_ledger_json(stale_review) + "\n",
    )
    receipt = tmp_path / "research.receipt"
    report = tmp_path / "research.md"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784024400\n",
    )
    write_private_report(
        report,
        "# Planned intraday actual research\n\n"
        "- result: ready\n"
        "- run key: actual-2026-07-14\n"
        f"- plan id: {planned.plan.plan_id}\n",
    )

    with pytest.raises(
        IntradayActualResearchAuditError,
        match="review_or_experiment_mismatch",
    ):
        _ = audit_intraday_actual_research(
            IntradayActualResearchAuditRequest(
                run_key="actual-2026-07-14",
                plan_path=planned.plan_path,
                research_receipt=receipt,
                research_report=report,
                expected_dataset_producer_commit_sha=(
                    request.dataset_producer_commit_sha
                ),
                expected_code_version=request.code_version,
                output_root=tmp_path / "audit",
            )
        )
