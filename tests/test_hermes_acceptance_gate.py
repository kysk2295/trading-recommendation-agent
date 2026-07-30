from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from trading_agent.acceptance_evidence import (
    AcceptanceEvidenceBuildRequest,
    AcceptanceEvidenceManifest,
    AcceptanceSessionEvidence,
    AcceptanceSessionKind,
    acceptance_artifact_sha256,
    build_acceptance_manifest,
    require_clean_repository_commit,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_acceptance_evidence import (
    HermesAcceptanceBuildRequest,
    InvalidHermesAcceptanceBuildError,
    build_hermes_acceptance_evidence,
    verify_hermes_acceptance_evidence,
)
from trading_agent.hermes_acceptance_gate import (
    HermesAcceptanceGateReason,
    HermesAcceptanceGateStatus,
    HermesAcceptanceSessionEvidence,
    assess_hermes_acceptance,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.hermes_operational_qa import HermesOperationalQaRequest, run_hermes_operational_qa
from trading_agent.kis_kr_session_calendar_models import (
    KrSessionCalendarPayload,
    KrSessionDay,
    kr_session_calendar_snapshot,
)
from trading_agent.private_stable_report import write_private_stable_report


def test_gate_waits_when_real_us_and_kr_session_counts_are_insufficient() -> None:
    # Given: the two distinct real sessions currently observed for each market.
    sessions = (
        _session("XNYS-2026-07-22", "us_equities"),
        _session("XNYS-2026-07-23", "us_equities"),
        _session("XKRX-2026-07-22", "kr_equities"),
        _session("XKRX-2026-07-23", "kr_equities"),
    )

    # When: the aggregate Hermes acceptance gate is assessed.
    result = assess_hermes_acceptance(sessions)

    # Then: current insufficient evidence cannot become an acceptance pass.
    assert result.status is HermesAcceptanceGateStatus.WAITING
    assert result.us_real_session_count == 2
    assert result.kr_real_session_count == 2


def test_gate_blocks_when_five_real_us_sessions_skip_a_regular_session() -> None:
    # Given: five US records that omit the completed 2026-07-22 NYSE session.
    sessions = (
        *(_session(f"XNYS-2026-07-{day}", "us_equities") for day in ("20", "21", "23", "24", "27")),
        *(_session(f"XKRX-2026-07-{day}", "kr_equities") for day in ("20", "21", "22", "23", "24")),
    )

    # When: the aggregate gate sees a missing real session in the US sequence.
    result = assess_hermes_acceptance(sessions)

    # Then: a non-consecutive five-session set is blocked, not accepted.
    assert result.status is HermesAcceptanceGateStatus.BLOCKED
    assert result.reasons == (HermesAcceptanceGateReason.NON_CONSECUTIVE_REAL_SESSIONS,)


def test_gate_blocks_mismatched_expected_and_projected_delivery_ids() -> None:
    # Given: a session whose artifact claims a projected delivery identity not in its expected set.
    session = _session("XNYS-2026-07-20", "us_equities").model_copy(update={"projected_delivery_ids": ("b" * 64,)})

    # When: reconciliation accounting is assessed.
    result = assess_hermes_acceptance((session,))

    # Then: the mismatch is a blocker even before enough sessions accumulate.
    assert result.status is HermesAcceptanceGateStatus.BLOCKED
    assert HermesAcceptanceGateReason.UNRECONCILED_DELIVERY in result.reasons


def test_gate_blocks_controlled_fixture_from_real_session_count() -> None:
    # Given: a controlled-fixture delivery record with otherwise complete local reconciliation.
    session = _session("XNYS-2026-07-20", "us_equities").model_copy(update={"kind": AcceptanceSessionKind.FIXTURE})

    # When: the aggregate gate is assessed.
    result = assess_hermes_acceptance((session,))

    # Then: fixture evidence is non-real and cannot contribute to a passing sequence.
    assert result.status is HermesAcceptanceGateStatus.BLOCKED
    assert result.us_real_session_count == 0
    assert HermesAcceptanceGateReason.NON_REAL_SESSION in result.reasons


def test_status_cli_reports_empty_current_delivery_ledger_as_waiting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a current delivery ledger with no aggregated real-session reports.
    from run_hermes_acceptance_gate import main

    database = tmp_path / "delivery.sqlite3"
    with HermesDeliveryStore(database).writer():
        pass

    # When: a manual operator requests its aggregate gate status.
    exit_code = main(("status", "--database", str(database)))

    # Then: the CLI emits a typed waiting result without inventing market sessions.
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "kr_real_session_count": 0,
        "reason": "missing_session_reports",
        "result": "waiting",
        "us_real_session_count": 0,
    }


def test_status_cli_blocks_a_missing_delivery_ledger(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an operator points status at a ledger path that has never been initialized.
    from run_hermes_acceptance_gate import main

    # When / Then: absent evidence is invalid rather than an invented empty ledger.
    assert main(("status", "--database", str(tmp_path / "missing.sqlite3"))) == 2
    assert json.loads(capsys.readouterr().out) == {
        "kr_real_session_count": 0,
        "reason": "invalid_acceptance_evidence",
        "result": "blocked",
        "us_real_session_count": 0,
    }


def test_status_cli_blocks_a_corrupt_delivery_ledger(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from run_hermes_acceptance_gate import main

    database = tmp_path / "corrupt.sqlite3"
    database.write_text("not a sqlite database\n", encoding="utf-8")
    assert main(("status", "--database", str(database))) == 2
    assert json.loads(capsys.readouterr().out) == {
        "kr_real_session_count": 0,
        "reason": "invalid_acceptance_evidence",
        "result": "blocked",
        "us_real_session_count": 0,
    }


def test_builder_and_verifier_accept_five_reconciled_real_sessions_per_market(tmp_path: Path) -> None:
    # Given: a clean repository with five consecutive real, fully reconciled sessions per market.
    repository = _clean_repository(tmp_path)
    calendar_path = _kr_calendar(repository)
    sessions = tuple(
        _session_with_artifact(repository, f"XNYS-2026-07-{day}", "us_equities")
        for day in ("20", "21", "22", "23", "24")
    ) + tuple(
        _session_with_artifact(repository, f"XKRX-2026-07-{day}", "kr_equities", calendar_path)
        for day in ("20", "21", "22", "23", "24")
    )
    request = HermesAcceptanceBuildRequest(
        generated_at=dt.datetime(2026, 7, 24, 17, tzinfo=dt.UTC),
        sessions=sessions,
        plugin_installation_path=Path("outputs/acceptance/hermes/plugin_installation.json"),
        query_and_alert_qa_path=Path("outputs/acceptance/hermes/query_and_alert_qa.md"),
        restart_and_provider_fault_path=Path("outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json"),
    )
    _plugin_receipt(repository)
    _operational_artifacts(tmp_path, repository)
    report = repository / "outputs/acceptance/hermes/delivery_reconciliation.json"
    manifest = repository / "outputs/acceptance/hermes/manifest.json"

    # When: the aggregate report and AC-001 manifest are built then verified.
    built = build_hermes_acceptance_evidence(request, repository, report, manifest)
    verified = verify_hermes_acceptance_evidence(report, manifest, repository)

    # Then: the generic hash/commit verifier and aggregate invariants both pass.
    assert built.manifest is not None
    assert verified.assessment.status is HermesAcceptanceGateStatus.PASSED


def test_verifier_rejects_a_tampered_source_session_even_with_a_refreshed_generic_manifest(tmp_path: Path) -> None:
    # Given: a previously passing bundle whose source session is changed after its aggregate report was written.
    repository, report, manifest, sessions = _passing_bundle(tmp_path)
    altered = sessions[0].model_copy(update={"acknowledged_delivery_ids": ("b" * 64,)})
    write_private_stable_report(repository / altered.reconciliation_artifact_path, altered.model_dump_json() + "\n")
    _refresh_manifest(repository, manifest, sessions)

    # When: an operator verifies the bundle after only generic artifact hashes were refreshed.
    with pytest.raises(InvalidHermesAcceptanceBuildError):
        _ = verify_hermes_acceptance_evidence(report, manifest, repository)

    # Then: re-parsed reconciliation content must still exactly equal the aggregate report sessions.


def test_verifier_rejects_a_tampered_kr_calendar_even_with_a_refreshed_generic_manifest(tmp_path: Path) -> None:
    # Given: a passing bundle whose private KIS calendar no longer proves the reported next KR session.
    repository, report, manifest, _ = _passing_bundle(tmp_path)
    calendar = _kr_calendar(repository, business_days=(20, 22, 23, 24, 25))
    assert calendar == Path("outputs/acceptance/hermes/kr-calendar.json")
    _refresh_manifest(repository, manifest, _read_sessions(repository))

    # When: the generic manifest is rebuilt around the altered private calendar file.
    with pytest.raises(InvalidHermesAcceptanceBuildError):
        _ = verify_hermes_acceptance_evidence(report, manifest, repository)

    # Then: the verifier re-hashes and replays the KIS next-open proof rather than trusting its prior report.


def test_builder_and_verifier_reject_noncanonical_paths_without_traceback(tmp_path: Path) -> None:
    # Given: valid request data but report and manifest destinations outside the fixed AC-001 artifact paths.
    repository, report, manifest, sessions = _passing_bundle(tmp_path)
    request = _request(sessions)

    # When / Then: path traversal or a sibling output is typed invalid evidence, never a relative-path traceback.
    with pytest.raises(InvalidHermesAcceptanceBuildError):
        _ = build_hermes_acceptance_evidence(request, repository, repository / "outside.json", manifest)
    with pytest.raises(InvalidHermesAcceptanceBuildError):
        _ = verify_hermes_acceptance_evidence(repository / "outside.json", manifest, repository)
    assert report.is_file()


def test_verifier_rejects_manifest_session_verifier_and_timestamp_tampering(tmp_path: Path) -> None:
    # Given: a passing report and generic manifest with each acceptance binding changed in turn.
    repository, report, manifest_path, _ = _passing_bundle(tmp_path)
    manifest = AcceptanceEvidenceManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    tampered_manifests = (
        manifest.model_copy(update={"sessions": ()}),
        manifest.model_copy(update={"verifier_version": "forged-verifier"}),
        manifest.model_copy(update={"generated_at": dt.datetime(2026, 7, 24, 17, 1, tzinfo=dt.UTC)}),
    )

    # When / Then: any manifest field that binds the report to AC-001 must remain exact.
    for tampered in tampered_manifests:
        write_private_stable_report(manifest_path, tampered.model_dump_json() + "\n")
        with pytest.raises(InvalidHermesAcceptanceBuildError):
            _ = verify_hermes_acceptance_evidence(report, manifest_path, repository)


def test_session_model_rejects_observation_time_from_a_different_market_date() -> None:
    # Given: an NYSE session ID paired with an observation timestamp from a later New York trading date.
    session = _session("XNYS-2026-07-20", "us_equities")
    payload = session.model_dump(mode="python") | {
        "observed_from": dt.datetime(2026, 7, 22, 16, tzinfo=dt.UTC),
        "observed_through": dt.datetime(2026, 7, 22, 16, tzinfo=dt.UTC),
    }

    # When / Then: the session model rejects a window outside the authoritative NYSE session bounds.
    with pytest.raises(ValidationError):
        _ = HermesAcceptanceSessionEvidence.model_validate(payload)


def test_verifier_rejects_plugin_receipt_observed_after_report_generation(tmp_path: Path) -> None:
    repository, report, manifest, _ = _passing_bundle(tmp_path)
    receipt_path = repository / "outputs/acceptance/hermes/plugin_installation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["observed_at"] = "2026-07-24T17:01:00Z"
    write_private_stable_report(receipt_path, json.dumps(receipt, sort_keys=True) + "\n")
    with pytest.raises(InvalidHermesAcceptanceBuildError):
        _ = verify_hermes_acceptance_evidence(report, manifest, repository)


def test_builder_rejects_arbitrary_session_artifact_text(tmp_path: Path) -> None:
    # Given: caller claims a reconciled session while its referenced artifact is arbitrary text.
    repository = _clean_repository(tmp_path)
    session = _session_with_artifact(repository, "XNYS-2026-07-20", "us_equities")
    write_private_stable_report(repository / session.reconciliation_artifact_path, "observed\n")
    request = HermesAcceptanceBuildRequest(
        generated_at=dt.datetime(2026, 7, 24, 17, tzinfo=dt.UTC),
        sessions=(session,),
        plugin_installation_path=Path("outputs/acceptance/hermes/plugin_installation.json"),
        query_and_alert_qa_path=Path("outputs/acceptance/hermes/query_and_alert_qa.md"),
        restart_and_provider_fault_path=Path("outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json"),
    )
    _required_artifacts(repository)

    # When / Then: a caller-supplied summary cannot substitute for a parsed reconciliation artifact.
    with pytest.raises(InvalidHermesAcceptanceBuildError):
        _ = build_hermes_acceptance_evidence(
            request,
            repository,
            repository / "outputs/acceptance/hermes/delivery_reconciliation.json",
            repository / "outputs/acceptance/hermes/manifest.json",
        )


def _session(session_id: str, market_id: Literal["us_equities", "kr_equities"]) -> HermesAcceptanceSessionEvidence:
    session_date = dt.date.fromisoformat(session_id[5:])
    observed_at = dt.datetime.combine(
        session_date,
        dt.time(15, 30) if market_id == "us_equities" else dt.time(1),
        tzinfo=dt.UTC,
    )
    return HermesAcceptanceSessionEvidence(
        session_id=session_id,
        market_id=market_id,
        kind=AcceptanceSessionKind.REAL,
        observed_from=observed_at,
        observed_through=observed_at,
        expected_delivery_ids=("a" * 64,),
        projected_delivery_ids=("a" * 64,),
        acknowledged_delivery_ids=("a" * 64,),
        terminal_delivery_ids=(),
        duplicate_delivery_ids=(),
        omitted_delivery_ids=(),
        unaccounted_delivery_ids=(),
        reconciliation_artifact_path=Path("outputs/acceptance/hermes/sessions/test.json"),
    )


def _session_with_artifact(
    repository: Path,
    session_id: str,
    market_id: Literal["us_equities", "kr_equities"],
    calendar_path: Path | None = None,
) -> HermesAcceptanceSessionEvidence:
    path = Path("outputs/acceptance/hermes/sessions") / f"{session_id}.json"
    session = _session(session_id, market_id).model_copy(
        update={"reconciliation_artifact_path": path, "kr_calendar_snapshot_path": calendar_path}
    )
    write_private_stable_report(repository / path, session.model_dump_json() + "\n")
    return session


def _artifact(repository: Path, name: str) -> Path:
    path = Path("outputs/acceptance/hermes") / name
    write_private_stable_report(repository / path, "observed\n")
    return path


def _kr_calendar(repository: Path, *, business_days: tuple[int, ...] = tuple(range(20, 26))) -> Path:
    path = Path("outputs/acceptance/hermes/kr-calendar.json")
    days = tuple(
        KrSessionDay(
            session_date=dt.date(2026, 7, day),
            weekday_code=str(day),
            business_day=day in business_days,
            trading_day=day in business_days,
            open_day=day in business_days,
            settlement_day=True,
        )
        for day in range(20, 26)
    )
    snapshot = kr_session_calendar_snapshot(
        KrSessionCalendarPayload(
            source_commit="885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc",
            adapter_version="kis-chk-holiday-v1",
            base_date=dt.date(2026, 7, 20),
            observed_at=dt.datetime(2026, 7, 20, 9, tzinfo=dt.timezone(dt.timedelta(hours=9))),
            receipt_sha256="f" * 64,
            days=days,
        )
    )
    write_private_stable_report(repository / path, snapshot.model_dump_json() + "\n")
    return path


def _required_artifacts(repository: Path) -> None:
    _plugin_receipt(repository)
    write_private_stable_report(
        repository / "outputs/acceptance/hermes/query_and_alert_qa.md",
        "\n".join(
            (
                "# Hermes query and alert QA",
                "- separate family count: 6",
                "- family names: opportunity_manager, day_trading, swing_trading, systematic_quant, "
                "derivatives_research, market_context",
                "- blended verdict: none",
                "- execution aggregate counts: 0/0/0/0",
                "- outbound summary leak count: 0",
                "- generated report leak count: 0",
                "",
            )
        ),
    )
    write_private_stable_report(
        repository / "outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json",
        json.dumps(
            {
                "controlled_fixture": True,
                "provider_incident": {
                    "fixture": "controlled_fixture",
                    "kind": "read_only_provider_outage",
                    "network_calls": 0,
                    "provider_mutation_count": 0,
                    "terminal": True,
                },
                "real_session": False,
                "restart": {
                    "acknowledged_or_terminal_count": 1,
                    "duplicate_count": 0,
                    "omission_count": 0,
                    "reply_lineage_verified": True,
                    "retry_after_expired_claim": True,
                    "same_delivery_identity": True,
                    "store_reopened": True,
                    "suppression_terminal_count": 1,
                    "unaccounted_count": 0,
                },
                "scenario": "controlled_fixture",
            }
        )
        + "\n",
    )


def _clean_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "qa@example.invalid")
    _git(repository, "config", "user.name", "QA")
    (repository / ".gitignore").write_text("outputs/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    plugin_manifest = repository / "integrations/hermes/trading-agent/plugin.yaml"
    plugin_manifest.parent.mkdir(parents=True)
    shutil.copyfile(Path("integrations/hermes/trading-agent/plugin.yaml"), plugin_manifest)
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return repository


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=repository, check=True, capture_output=True, text=True)


def _plugin_receipt(repository: Path) -> None:
    write_private_stable_report(
        repository / "outputs/acceptance/hermes/plugin_installation.json",
        json.dumps(
            {
                "enabled": True,
                "installed_version": "1.3.0",
                "observed_at": "2026-07-24T17:00:00Z",
                "plugin_manifest_path": "integrations/hermes/trading-agent/plugin.yaml",
                "plugin_manifest_sha256": acceptance_artifact_sha256(
                    repository, Path("integrations/hermes/trading-agent/plugin.yaml")
                ),
                "profile": "stockagent",
                "runtime_tools": [
                    "trading_agent_query",
                    "trading_agent_status",
                    "trading_agent_arm_prepare",
                    "trading_agent_arm_confirm",
                    "trading_agent_arm_revoke",
                ],
                "source_commit_sha": require_clean_repository_commit(repository),
            },
            sort_keys=True,
        )
        + "\n",
    )


def _operational_artifacts(tmp_path: Path, repository: Path) -> None:
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with delivery.writer():
        pass
    execution = ExecutionStore(tmp_path / "execution.sqlite3")
    with execution.writer():
        pass
    _ = run_hermes_operational_qa(
        HermesOperationalQaRequest(
            delivery_store=delivery.path,
            execution_store=execution.path,
            output_root=repository / "outputs",
            observed_at=dt.datetime(2026, 7, 24, 17, tzinfo=dt.UTC),
        )
    )


def _request(sessions: tuple[HermesAcceptanceSessionEvidence, ...]) -> HermesAcceptanceBuildRequest:
    return HermesAcceptanceBuildRequest(
        generated_at=dt.datetime(2026, 7, 24, 17, tzinfo=dt.UTC),
        sessions=sessions,
        plugin_installation_path=Path("outputs/acceptance/hermes/plugin_installation.json"),
        query_and_alert_qa_path=Path("outputs/acceptance/hermes/query_and_alert_qa.md"),
        restart_and_provider_fault_path=Path("outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json"),
    )


def _passing_bundle(tmp_path: Path) -> tuple[Path, Path, Path, tuple[HermesAcceptanceSessionEvidence, ...]]:
    repository = _clean_repository(tmp_path)
    calendar_path = _kr_calendar(repository)
    sessions = tuple(
        _session_with_artifact(repository, f"XNYS-2026-07-{day}", "us_equities")
        for day in ("20", "21", "22", "23", "24")
    ) + tuple(
        _session_with_artifact(repository, f"XKRX-2026-07-{day}", "kr_equities", calendar_path)
        for day in ("20", "21", "22", "23", "24")
    )
    _plugin_receipt(repository)
    _operational_artifacts(tmp_path, repository)
    report = repository / "outputs/acceptance/hermes/delivery_reconciliation.json"
    manifest = repository / "outputs/acceptance/hermes/manifest.json"
    _ = build_hermes_acceptance_evidence(_request(sessions), repository, report, manifest)
    return repository, report, manifest, sessions


def _read_sessions(repository: Path) -> tuple[HermesAcceptanceSessionEvidence, ...]:
    return tuple(
        HermesAcceptanceSessionEvidence.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((repository / "outputs/acceptance/hermes/sessions").glob("*.json"))
    )


def _refresh_manifest(
    repository: Path,
    manifest: Path,
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> None:
    _ = build_acceptance_manifest(
        AcceptanceEvidenceBuildRequest(
            criterion_id="AC-001",
            policy_version="hermes-aggregate-acceptance-v1",
            verifier_version="hermes-aggregate-acceptance-v1",
            generated_at=dt.datetime(2026, 7, 24, 17, tzinfo=dt.UTC),
            sessions=tuple(
                AcceptanceSessionEvidence(
                    session_id=session.session_id,
                    market_id=session.market_id,
                    kind=session.kind,
                    observed_from=session.observed_from,
                    observed_through=session.observed_through,
                )
                for session in sessions
            ),
            artifact_paths=(
                Path("outputs/acceptance/hermes/delivery_reconciliation.json"),
                Path("outputs/acceptance/hermes/plugin_installation.json"),
                Path("outputs/acceptance/hermes/query_and_alert_qa.md"),
                Path("outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json"),
                Path("outputs/acceptance/soak/us_five_session_report.json"),
                Path("outputs/acceptance/soak/kr_five_session_report.json"),
            ),
        ),
        repository,
        manifest,
    )
