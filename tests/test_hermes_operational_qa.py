from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import run_hermes_operational_qa as cli
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind, build_hermes_delivery_event
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.hermes_operational_qa import (
    HermesOperationalQaRequest,
    InvalidHermesOperationalQaError,
    run_hermes_operational_qa,
)

OBSERVED_AT = dt.datetime(2026, 7, 30, 15, 0, tzinfo=dt.UTC)


def test_operational_qa_runs_controlled_restart_and_provider_fault_without_network(tmp_path: Path) -> None:
    # Given: initialized local-only ledgers containing one safe, separate Hermes opinion.
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with delivery.writer() as writer:
        assert writer.append_event(
            build_hermes_delivery_event(
                kind=HermesDeliveryKind.RESEARCH,
                source_event_id="qa-opportunity",
                market_id="US",
                lane_id="qa-lane",
                occurred_at=OBSERVED_AT,
                payload_sha256="a" * 64,
                rendered_text="sanitized outbound summary",
                agent_family="opportunity_manager",
                instrument_id="QA-LOCAL",
                status="ready",
            )
        ).inserted
    execution = ExecutionStore(tmp_path / "execution.sqlite3")
    with execution.writer():
        pass

    # When: the isolated QA runner performs its fixture-only reconciliation.
    result = run_hermes_operational_qa(
        HermesOperationalQaRequest(
            delivery_store=delivery.path,
            execution_store=execution.path,
            output_root=tmp_path / "outputs",
            observed_at=OBSERVED_AT,
        )
    )

    # Then: only sanitized aggregate evidence and distinct six-family query results are published.
    reconciliation = json.loads(result.reconciliation_path.read_text(encoding="utf-8"))
    report = result.query_report_path.read_text(encoding="utf-8")
    assert reconciliation["scenario"] == "controlled_fixture"
    assert reconciliation["real_session"] is False
    assert reconciliation["delivery"] == {
        "acknowledgement_count": 0,
        "attempt_count": 0,
        "dead_letter_count": 0,
        "event_count": 1,
    }
    assert reconciliation["restart"]["same_delivery_identity"] is True
    assert reconciliation["restart"]["retry_after_expired_claim"] is True
    assert reconciliation["restart"]["store_reopened"] is True
    assert reconciliation["restart"]["duplicate_count"] == 0
    assert reconciliation["restart"]["omission_count"] == 0
    assert reconciliation["restart"]["unaccounted_count"] == 0
    assert reconciliation["restart"]["reply_lineage_verified"] is True
    assert reconciliation["restart"]["suppression_terminal_count"] == 1
    assert reconciliation["provider_incident"] == {
        "fixture": "controlled_fixture",
        "kind": "read_only_provider_outage",
        "network_calls": 0,
        "provider_mutation_count": 0,
        "terminal": True,
    }
    assert reconciliation["query"]["family_count"] == 6
    assert reconciliation["query"]["blended_verdict"] is None
    assert "opportunity_manager" in report
    assert "sanitized outbound summary" not in report
    assert str(tmp_path) not in report
    assert "account_fingerprint" not in result.reconciliation_path.read_text(encoding="utf-8")


def test_operational_qa_rejects_a_leaking_outbound_summary_before_publication(tmp_path: Path) -> None:
    # Given: an otherwise valid local delivery ledger whose summary carries a credential-shaped value.
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with delivery.writer() as writer:
        assert writer.append_event(
            build_hermes_delivery_event(
                kind=HermesDeliveryKind.RESEARCH,
                source_event_id="qa-leak",
                market_id="US",
                lane_id="qa-lane",
                occurred_at=OBSERVED_AT,
                payload_sha256="b" * 64,
                rendered_text="authorization=Bearer fixture-token",
                agent_family="opportunity_manager",
                instrument_id="QA-LOCAL",
                status="ready",
            )
        ).inserted

    # When / Then: the boundary rejects it without creating acceptance artifacts.
    with pytest.raises(InvalidHermesOperationalQaError):
        _ = run_hermes_operational_qa(
            HermesOperationalQaRequest(
                delivery_store=delivery.path,
                execution_store=None,
                output_root=tmp_path / "outputs",
                observed_at=OBSERVED_AT,
            )
        )
    assert not (tmp_path / "outputs").exists()


def test_operational_qa_blocks_missing_stores_before_artifact_publication(tmp_path: Path) -> None:
    # Given: no delivery or execution ledger exists at either required input path.
    output = tmp_path / "outputs"

    # When / Then: acceptance evidence rejects the missing inputs without creating reports.
    with pytest.raises(InvalidHermesOperationalQaError):
        _ = run_hermes_operational_qa(
            HermesOperationalQaRequest(
                delivery_store=tmp_path / "missing-delivery.sqlite3",
                execution_store=tmp_path / "missing-execution.sqlite3",
                output_root=output,
                observed_at=OBSERVED_AT,
            )
        )
    assert not output.exists()


def test_operational_qa_cli_help_bad_store_and_offline_happy_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the public CLI and an intentionally malformed supplied delivery store.
    malformed = tmp_path / "malformed.sqlite3"
    malformed.write_text("not a sqlite database", encoding="utf-8")
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with delivery.writer():
        pass
    execution = ExecutionStore(tmp_path / "execution.sqlite3")
    with execution.writer():
        pass

    # When: help, the rejected store, and a fixture-only invocation run.
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])
    help_output = capsys.readouterr().out
    blocked = cli.main(
        [
            "--delivery-store",
            str(malformed),
            "--output-root",
            str(tmp_path / "blocked-output"),
            "--observed-at",
            OBSERVED_AT.isoformat(),
        ]
    )
    blocked_output = capsys.readouterr().out
    recorded = cli.main(
        [
            "--output-root",
            str(tmp_path / "outputs"),
            "--delivery-store",
            str(delivery.path),
            "--execution-store",
            str(execution.path),
            "--observed-at",
            OBSERVED_AT.isoformat(),
        ]
    )
    recorded_output = capsys.readouterr().out

    # Then: the binary surfaces are available, sanitized, and entirely local.
    assert raised.value.code == 0
    assert "--observed-at" in help_output
    assert blocked == 2
    assert json.loads(blocked_output) == {"reason": "invalid_operational_qa_input", "result": "blocked"}
    assert recorded == 0
    assert json.loads(recorded_output) == {"result": "recorded"}
    assert (tmp_path / "outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json").is_file()
