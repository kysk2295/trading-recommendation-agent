from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pytest import CaptureFixture

import run_intraday_promotion as cli
from tests.test_intraday_promotion_evidence import OBSERVED_AT, SELECTED, SESSION, _artifacts
from tests.test_lifecycle_controller import ORB_VERSION, _seed_base_sources
from trading_agent.experiment_ledger_keys import strategy_version_registration_key
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.intraday_promotion_evidence import IntradayPromotionEvidencePaths


def test_cli_runs_blocked_assessment_approval_transition_and_replay(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    # Given: a real challenger ledger and six private canonical evidence artifacts
    ledger = _ledger(tmp_path)
    artifacts = _artifacts(tmp_path / "evidence")
    common = _common_arguments(ledger.path, artifacts.paths)

    # When: an operator assesses, approves, controls, and replays through the CLI
    assert (
        cli.main(
            ("assess", *common, "--output-dir", str(tmp_path / "assessments"), "--timestamp", OBSERVED_AT.isoformat())
        )
        == 0
    )
    assessed = json.loads(capsys.readouterr().out)
    assessment = next((tmp_path / "assessments").glob("intraday_promotion_assessment_*.json"))
    approved_at = OBSERVED_AT + dt.timedelta(minutes=5)
    assert (
        cli.main(
            (
                "approve",
                "--assessment",
                str(assessment),
                "--approver",
                "operator_1",
                "--output-dir",
                str(tmp_path / "approvals"),
                "--timestamp",
                approved_at.isoformat(),
            )
        )
        == 0
    )
    approved = json.loads(capsys.readouterr().out)
    approval = next((tmp_path / "approvals").glob("intraday_promotion_approval_*.json"))
    control = (
        "control",
        *common,
        "--assessment",
        str(assessment),
        "--approval",
        str(approval),
        "--timestamp",
        (approved_at + dt.timedelta(minutes=5)).isoformat(),
    )
    assert cli.main(control) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(control) == 0
    replay = json.loads(capsys.readouterr().out)

    # Then: JSON is path-free and mutation counters prove exactly-once local control
    assert assessed["result"] == "manual_approval_pending"
    assert assessed["blockers"] == ["manual_approval_required"]
    assert approved["result"] == "approved"
    assert (first["authority_bindings_created"], first["lifecycle_events_created"]) == (1, 1)
    assert (replay["authority_bindings_created"], replay["lifecycle_events_created"]) == (0, 0)
    assert all("/" not in payload["identifier"] for payload in (assessed, approved, first, replay))
    assert all(
        payload["network_access"] == payload["broker_mutations"] == 0 for payload in (assessed, approved, first, replay)
    )


def _common_arguments(ledger: Path, paths: IntradayPromotionEvidencePaths) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(ledger),
        "--audit",
        str(paths.audit),
        "--comparison",
        str(paths.comparison),
        "--diagnostics",
        str(paths.diagnostics),
        "--plateau",
        str(paths.plateau),
        "--broker-shadow",
        str(paths.broker_shadow),
        "--sip",
        str(paths.sip),
        "--session-date",
        SESSION.isoformat(),
    )


def _ledger(tmp_path: Path) -> ExperimentLedgerStore:
    _, _, ledger = _seed_base_sources(tmp_path)
    original = next(
        stored.registration
        for stored in ledger.strategy_versions()
        if stored.registration.strategy_version == ORB_VERSION
    )
    version = original.model_copy(update={"strategy_version": SELECTED})
    hypothesis_key = next(
        str(stored.registration_key)
        for stored in ledger.hypotheses()
        if stored.registration.hypothesis_id == version.hypothesis_id
    )
    registration = StrategyLifecycleEvent(
        strategy_version=SELECTED,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="test_import_v1",
        decision_session_date=dt.date(2026, 7, 14),
        effective_session_date=dt.date(2026, 7, 15),
        decided_at=dt.datetime(2026, 7, 14, 20, tzinfo=dt.UTC),
        evidence_keys=tuple(
            sorted((hypothesis_key, version.experiment_scope_key, str(strategy_version_registration_key(version))))
        ),
        reason_codes=("existing_contract_import",),
        previous_event_key=None,
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_version(version)
        assert writer.append_lifecycle_event(registration)
    registered = ledger.lifecycle_events(SELECTED)[-1]
    challenger = StrategyLifecycleEvent(
        strategy_version=SELECTED,
        sequence=2,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        to_state=StrategyLifecycleState.CHALLENGER,
        policy_version="test_challenger_v1",
        decision_session_date=dt.date(2026, 7, 15),
        effective_session_date=SESSION,
        decided_at=dt.datetime(2026, 7, 15, 20, 30, tzinfo=dt.UTC),
        evidence_keys=tuple(sorted((str(registered.event_key), "d" * 64))),
        reason_codes=("comparison_ready",),
        previous_event_key=registered.event_key,
    )
    with ledger.writer() as writer:
        assert writer.append_lifecycle_event(challenger)
    return ledger
