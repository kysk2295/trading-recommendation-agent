from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.intraday_research_input_binding_fixtures import NOW
from tests.test_intraday_actual_research import _request
from tests.test_intraday_actual_research_plan import _spec
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_actual_research_audit import (
    audit_intraday_actual_research,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditArtifact,
    IntradayActualResearchAuditError,
    IntradayActualResearchAuditPayload,
    IntradayActualResearchAuditRequest,
)
from trading_agent.intraday_actual_research_plan import (
    run_planned_intraday_actual_research,
)
from trading_agent.intraday_equal_risk_comparison_models import (
    EqualRiskComparisonStatus,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    IntradayOverfitDiagnosticsStatus,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchStrategyBinding,
)
from trading_agent.private_report import write_private_report
from trading_agent.research_hypothesis_registration import (
    register_research_hypothesis_manifest,
)
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)
from trading_agent.strategy_factory import StrategyMode

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_intraday_actual_research_audit.py"
HOD_SOURCE = PROJECT / "examples/research/us-hod-breakout-source-v2.json"
GAP_SOURCE = PROJECT / "examples/research/us-gap-and-go-source-v2.json"


def test_actual_research_audit_cli_exposes_exact_terminal_boundary() -> None:
    completed = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--run-key" in completed.stdout
    assert "--plan" in completed.stdout
    assert "--research-receipt" in completed.stdout
    assert "--research-report" in completed.stdout
    assert "--expected-dataset-producer-commit-sha" in completed.stdout
    assert "--expected-code-version" in completed.stdout
    assert "--output-dir" in completed.stdout


def test_actual_research_audit_cli_proves_exact_terminal_chain(
    tmp_path: Path,
) -> None:
    request, _ = _request(tmp_path)
    planned = run_planned_intraday_actual_research(
        _spec(request, run_key="actual-2026-07-14"),
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
        "- run key: actual-2026-07-14\n"
        f"- plan id: {planned.plan.plan_id}\n",
    )

    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--run-key",
            "actual-2026-07-14",
            "--plan",
            str(planned.plan_path),
            "--research-receipt",
            str(receipt),
            "--research-report",
            str(report),
            "--expected-dataset-producer-commit-sha",
            request.dataset_producer_commit_sha,
            "--expected-code-version",
            request.code_version,
            "--output-dir",
            str(output),
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    artifacts = tuple(output.glob("intraday_actual_research_audit_*.json"))
    audit = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert len(artifacts) == 1
    assert audit["payload"]["run_key"] == "actual-2026-07-14"
    assert audit["payload"]["dataset_input_sha256"] == planned.actual.catalog.dataset.input_sha256
    assert audit["payload"]["dataset_producer_commit_sha"] == request.dataset_producer_commit_sha
    assert audit["payload"]["strategy_code_version"] == request.code_version
    assert audit["payload"]["foundation_sha256s"] == list(planned.actual.binding.foundation_sha256s)
    assert len(audit["payload"]["trial_ids"]) == 1
    assert len(audit["payload"]["review_artifact_ids"]) == 1
    assert audit["payload"]["reviewer_decisions"] == ["hold"]
    assert audit["payload"]["comparison_artifact_id"] is None
    assert audit["payload"]["comparison_status"] is None
    assert audit["payload"]["overfit_diagnostics_artifact_id"] is None
    assert audit["payload"]["overfit_diagnostics_status"] is None
    assert audit["schema_version"] == 3
    assert audit["payload"]["schema_version"] == 3
    assert audit["payload"]["automatic_state_change_allowed"] is False
    assert audit["payload"]["order_authority_change_allowed"] is False
    assert audit["payload"]["allocation_change_allowed"] is False
    assert "- result: ready" in (output / "intraday_actual_research_audit_ko.md").read_text(encoding="utf-8")
    assert "- equal-risk comparison: not_applicable" in (output / "intraday_actual_research_audit_ko.md").read_text(
        encoding="utf-8"
    )
    assert "- DSR/PBO diagnostics: not_applicable" in (
        output / "intraday_actual_research_audit_ko.md"
    ).read_text(encoding="utf-8")
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (*artifacts, output / "intraday_actual_research_audit_ko.md")
    )

    legacy_raw = audit["payload"].copy()
    legacy_raw["schema_version"] = 2
    del legacy_raw["overfit_diagnostics_artifact_id"]
    del legacy_raw["overfit_diagnostics_status"]
    legacy_payload = IntradayActualResearchAuditPayload.model_validate(legacy_raw)
    legacy_id = hashlib.sha256(
        canonical_experiment_ledger_json(legacy_payload).encode()
    ).hexdigest()
    legacy_artifact = IntradayActualResearchAuditArtifact(
        schema_version=2,
        artifact_id=legacy_id,
        payload=legacy_payload,
    )
    assert legacy_artifact.schema_version == 2
    assert legacy_artifact.payload.schema_version == 2


def test_actual_research_audit_preserves_three_strategy_foundation_order(
    tmp_path: Path,
) -> None:
    request, ledger = _request(tmp_path)
    _ = register_research_hypothesis_manifest(HOD_SOURCE, ledger)
    _ = register_research_hypothesis_manifest(GAP_SOURCE, ledger)
    queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    queue_path, _ = publish_source_driven_hypothesis_queue(
        tmp_path / "queue-three",
        queue,
    )
    cards = {item.hypothesis_id: item.card_key for item in queue.snapshot.items}
    bindings = (
        IntradayResearchStrategyBinding(
            strategy=StrategyMode.VWAP_RECLAIM,
            strategy_version="actual_vwap_reclaim_forward_v1",
            queue_card_key=cards["H-MOM-VWAP-SOURCE-002"],
        ),
        IntradayResearchStrategyBinding(
            strategy=StrategyMode.HOD_BREAKOUT,
            strategy_version="actual_hod_breakout_forward_v1",
            queue_card_key=cards["H-MOM-HOD-SOURCE-002"],
        ),
        IntradayResearchStrategyBinding(
            strategy=StrategyMode.GAP_AND_GO,
            strategy_version="actual_gap_and_go_forward_v1",
            queue_card_key=cards["H-MOM-GAP-SOURCE-002"],
        ),
    )
    request = replace(
        request,
        strategy_bindings=bindings,
        paths=replace(request.paths, source_queue_artifact=queue_path),
    )
    planned = run_planned_intraday_actual_research(
        _spec(request, run_key="actual-three-2026-07-14"),
        plan_root=tmp_path / "plans-three",
        queue_root=tmp_path / "planned-queue-three",
        observed_at=NOW,
    )
    receipt = tmp_path / "research-three.receipt"
    report = tmp_path / "research-three.md"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784786400\n",
    )
    write_private_report(
        report,
        "# Planned intraday actual research\n\n"
        "- result: ready\n"
        "- run key: actual-three-2026-07-14\n"
        f"- plan id: {planned.plan.plan_id}\n",
    )

    result = audit_intraday_actual_research(
        IntradayActualResearchAuditRequest(
            run_key="actual-three-2026-07-14",
            plan_path=planned.plan_path,
            research_receipt=receipt,
            research_report=report,
            expected_dataset_producer_commit_sha=(request.dataset_producer_commit_sha),
            expected_code_version=request.code_version,
            output_root=tmp_path / "audit-three",
        )
    )

    assert result.artifact.payload.foundation_sha256s == (planned.actual.binding.foundation_sha256s)
    assert len(result.artifact.payload.trial_ids) == 3
    assert tuple(item.value for item in result.artifact.payload.reviewer_decisions) == ("hold", "hold", "hold")
    assert result.artifact.payload.comparison_artifact_id is not None
    assert result.artifact.payload.comparison_status is EqualRiskComparisonStatus.COLLECTING
    assert result.artifact.payload.overfit_diagnostics_artifact_id is not None
    assert (
        result.artifact.payload.overfit_diagnostics_status
        is IntradayOverfitDiagnosticsStatus.COLLECTING
    )
    comparisons = tuple((tmp_path / "audit-three").glob("intraday_equal_risk_comparison_*.json"))
    assert len(comparisons) == 1
    comparison = json.loads(comparisons[0].read_text(encoding="utf-8"))
    assert comparison["artifact_id"] == (result.artifact.payload.comparison_artifact_id)
    assert [item["trial_id"] for item in comparison["payload"]["candidates"]] == sorted(
        result.artifact.payload.trial_ids
    )
    assert stat.S_IMODE(comparisons[0].stat().st_mode) == 0o600
    diagnostics = tuple(
        (tmp_path / "audit-three").glob(
            "intraday_overfit_diagnostics_*.json"
        )
    )
    assert len(diagnostics) == 1
    diagnostic = json.loads(diagnostics[0].read_text(encoding="utf-8"))
    assert diagnostic["artifact_id"] == (
        result.artifact.payload.overfit_diagnostics_artifact_id
    )
    assert diagnostic["payload"]["statistics"]["status"] == "collecting"
    assert len(diagnostic["payload"]["statistics"]["candidates"]) == 3
    assert stat.S_IMODE(diagnostics[0].stat().st_mode) == 0o600

    early_receipt = tmp_path / "research-three-early.receipt"
    write_private_report(
        early_receipt,
        "exit_code=0\ncompleted_at_epoch=1784024400\n",
    )
    with pytest.raises(
        IntradayActualResearchAuditError,
        match="invalid_terminal_evidence",
    ):
        _ = audit_intraday_actual_research(
            IntradayActualResearchAuditRequest(
                run_key="actual-three-2026-07-14",
                plan_path=planned.plan_path,
                research_receipt=early_receipt,
                research_report=report,
                expected_dataset_producer_commit_sha=(request.dataset_producer_commit_sha),
                expected_code_version=request.code_version,
                output_root=tmp_path / "audit-three-early",
            )
        )
    assert not tuple((tmp_path / "audit-three-early").glob("intraday_equal_risk_comparison_*.json"))
    assert not tuple(
        (tmp_path / "audit-three-early").glob(
            "intraday_overfit_diagnostics_*.json"
        )
    )

    cli_output = tmp_path / "audit-three-cli"
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--run-key",
            "actual-three-2026-07-14",
            "--plan",
            str(planned.plan_path),
            "--research-receipt",
            str(receipt),
            "--research-report",
            str(report),
            "--expected-dataset-producer-commit-sha",
            request.dataset_producer_commit_sha,
            "--expected-code-version",
            request.code_version,
            "--output-dir",
            str(cli_output),
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert "- equal-risk comparison: collecting" in (cli_output / "intraday_actual_research_audit_ko.md").read_text(
        encoding="utf-8"
    )
    assert "- DSR/PBO diagnostics: collecting" in (
        cli_output / "intraday_actual_research_audit_ko.md"
    ).read_text(encoding="utf-8")
    assert len(tuple(cli_output.glob("intraday_equal_risk_comparison_*.json"))) == 1
    assert len(
        tuple(cli_output.glob("intraday_overfit_diagnostics_*.json"))
    ) == 1


def test_actual_research_audit_rejects_plan_producer_identity_drift(
    tmp_path: Path,
) -> None:
    request, _ = _request(tmp_path)
    planned = run_planned_intraday_actual_research(
        _spec(request, run_key="actual-2026-07-14"),
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW,
    )

    with pytest.raises(
        IntradayActualResearchAuditError,
        match="plan_identity_mismatch",
    ):
        _ = audit_intraday_actual_research(
            IntradayActualResearchAuditRequest(
                run_key="actual-2026-07-14",
                plan_path=planned.plan_path,
                research_receipt=tmp_path / "not-read.receipt",
                research_report=tmp_path / "not-read.md",
                expected_dataset_producer_commit_sha="f" * 40,
                expected_code_version=request.code_version,
                output_root=tmp_path / "audit",
            )
        )
