from __future__ import annotations

import json
import stat
from pathlib import Path

from tests.intraday_research_input_binding_fixtures import NOW
from tests.test_intraday_actual_research import _request
from tests.test_intraday_actual_research_plan import _spec
from trading_agent.intraday_actual_research_audit import (
    audit_intraday_actual_research,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditRequest,
)
from trading_agent.intraday_actual_research_plan import (
    run_planned_intraday_actual_research,
)
from trading_agent.private_report import write_private_report


def test_terminal_audit_publishes_parameter_plateau_evidence(
    tmp_path: Path,
) -> None:
    request, _ = _request(tmp_path)
    planned = run_planned_intraday_actual_research(
        _spec(request, run_key="actual-plateau-2026-07-14"),
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW,
    )
    receipt = tmp_path / "research.receipt"
    report = tmp_path / "research.md"
    output = tmp_path / "audit"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784786400\n",
    )
    write_private_report(
        report,
        "# Planned intraday actual research\n\n"
        "- result: ready\n"
        "- run key: actual-plateau-2026-07-14\n"
        f"- plan id: {planned.plan.plan_id}\n",
    )

    result = audit_intraday_actual_research(
        IntradayActualResearchAuditRequest(
            run_key="actual-plateau-2026-07-14",
            plan_path=planned.plan_path,
            research_receipt=receipt,
            research_report=report,
            expected_dataset_producer_commit_sha=(
                request.dataset_producer_commit_sha
            ),
            expected_code_version=request.code_version,
            output_root=output,
        )
    )

    artifacts = tuple(
        output.glob("intraday_parameter_plateau_*.json")
    )
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    plateau_status = result.artifact.payload.parameter_plateau_status
    assert len(artifacts) == 1
    assert result.artifact.payload.parameter_plateau_artifact_id == (
        artifact["artifact_id"]
    )
    assert plateau_status is not None
    assert plateau_status.value == "collecting"
    assert artifact["payload"]["data_version"] == (
        planned.actual.catalog.dataset.input_sha256
    )
    assert artifact["payload"]["manifest_sha256"] == (
        planned.actual.binding.manifest_sha256
    )
    assert artifact["payload"]["status"] == "collecting"
    assert len(artifact["payload"]["analyses"]) == 1
    assert len(artifact["payload"]["analyses"][0]["variants"]) == 7
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    assert artifact["payload"]["automatic_state_change_allowed"] is False
    assert artifact["payload"]["order_authority_change_allowed"] is False
    assert artifact["payload"]["allocation_change_allowed"] is False
