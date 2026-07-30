from __future__ import annotations

import datetime as dt
import hashlib
import json
import stat
import subprocess
from pathlib import Path

import pytest

from trading_agent.acceptance_evidence import (
    AcceptanceArtifactEvidence,
    AcceptanceEvidenceManifest,
    AcceptanceSessionKind,
    verify_acceptance_manifest,
)
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_day_acceptance_evidence import (
    InvalidUsDayAcceptanceEvidenceError,
    UsDayAcceptanceBuildRequest,
    UsDaySessionTerminal,
    UsDayTerminalStatus,
    build_three_session_report,
    write_us_day_acceptance_evidence,
)
from trading_agent.us_day_operating_models import UsDayOperatingTransition

AT = dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.UTC)


def test_ten_censored_sessions_do_not_complete_natural_paper_gate() -> None:
    terminals = tuple(_terminal(index, natural=False) for index in range(10))

    report = build_three_session_report(terminals)

    assert report.delivery_subgate_passed is True
    assert report.natural_paper_lifecycle_passed is False
    assert report.operating_product_complete is False


def test_three_real_sessions_with_one_natural_lifecycle_complete_us_subgate() -> None:
    terminals = (_terminal(0, natural=True), _terminal(1, natural=False), _terminal(2, natural=False))

    report = build_three_session_report(terminals)

    assert report.eligible_session_count == 3
    assert report.delivery_subgate_passed is True
    assert report.natural_paper_lifecycle_passed is True
    assert report.operating_product_complete is True


def test_fixture_session_is_excluded_from_real_scheduled_session_count() -> None:
    fixture = _terminal(2, natural=True).model_copy(
        update={"session_kind": AcceptanceSessionKind.FIXTURE, "fixture_label": "fake_broker"}
    )

    report = build_three_session_report((_terminal(0, natural=False), _terminal(1, natural=False), fixture))

    assert report.eligible_session_count == 2
    assert report.operating_product_complete is False


def test_duplicate_daily_terminal_is_rejected() -> None:
    terminal = _terminal(0, natural=False)

    with pytest.raises(InvalidUsDayAcceptanceEvidenceError):
        _ = build_three_session_report((terminal, terminal))


def test_evidence_writer_emits_five_commit_bound_private_json_files(tmp_path: Path) -> None:
    repository = _clean_repository(tmp_path)
    commit_sha = _git(repository, "rev-parse", "HEAD")
    terminal_paths: list[Path] = []
    for index in range(3):
        relative = Path(f"outputs/acceptance/us_day/sessions/session-{index}.json")
        terminal = _terminal(index, natural=index == 0).model_copy(update={"commit_sha": commit_sha})
        source = repository / terminal.source_artifacts[0].path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source-{index}", encoding="utf-8")
        write_private_stable_report(repository / relative, terminal.model_dump_json(indent=2) + "\n")
        terminal_paths.append(relative)
    request = UsDayAcceptanceBuildRequest(
        repository=repository,
        terminal_paths=tuple(terminal_paths),
        generated_at=AT + dt.timedelta(days=3),
    )

    bundle = write_us_day_acceptance_evidence(request)

    outputs = (
        bundle.three_session_report_path,
        bundle.natural_paper_lifecycle_path,
        bundle.final_reconciliation_path,
        bundle.hermes_outcome_receipt_path,
        bundle.manifest_path,
    )
    assert all(path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600 for path in outputs)
    for path in outputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["commit_sha"] == commit_sha
        assert payload["policy_version"] == "us-day-operating-v1"
        assert payload["fixture_labels"] == ["real_session"]
        assert len(payload["source_artifact_hashes"]) >= 1
    manifest = AcceptanceEvidenceManifest.model_validate_json(bundle.manifest_path.read_text(encoding="utf-8"))
    verify_acceptance_manifest(
        manifest,
        repository,
        require_clean_commit=True,
        require_session_binding=True,
    )


def test_evidence_writer_binds_mixed_deployment_commits_to_each_session(tmp_path: Path) -> None:
    # Given: three terminal files produced by distinct historical deployments.
    repository = _clean_repository(tmp_path)
    terminal_commits: list[str] = []
    for index in range(3):
        (repository / f"deployment-{index}.txt").write_text(f"deployment-{index}\n", encoding="utf-8")
        _ = _git(repository, "add", ".")
        _ = _git(repository, "commit", "-m", f"deployment-{index}")
        terminal_commits.append(_git(repository, "rev-parse", "HEAD"))
    (repository / "verifier.txt").write_text("verifier\n", encoding="utf-8")
    _ = _git(repository, "add", ".")
    _ = _git(repository, "commit", "-m", "verifier")
    verifier_commit = _git(repository, "rev-parse", "HEAD")
    terminal_paths = _write_terminals(repository, tuple(terminal_commits))
    request = UsDayAcceptanceBuildRequest(repository=repository, terminal_paths=terminal_paths, generated_at=AT)

    # When: the clean current verifier writes the acceptance bundle.
    bundle = write_us_day_acceptance_evidence(request)

    # Then: every envelope binds each session to its historical terminal commit.
    expected_bindings = [
        {"session_id": f"XNYS-{(dt.date(2026, 7, 20) + dt.timedelta(days=index)).isoformat()}",
         "terminal_commit_sha": commit_sha}
        for index, commit_sha in enumerate(terminal_commits)
    ]
    for path in (
        bundle.three_session_report_path,
        bundle.natural_paper_lifecycle_path,
        bundle.final_reconciliation_path,
        bundle.hermes_outcome_receipt_path,
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["commit_sha"] == verifier_commit
        assert payload["session_commit_bindings"] == expected_bindings
    assert bundle.manifest.commit_sha == verifier_commit
    assert bundle.manifest.source_artifact_hashes == bundle.report.source_artifact_hashes


def test_evidence_writer_rejects_duplicate_session_ids(tmp_path: Path) -> None:
    # Given: otherwise valid terminal files sharing one session identity.
    repository = _clean_repository(tmp_path)
    commit_sha = _git(repository, "rev-parse", "HEAD")
    terminal_paths = _write_terminals(
        repository,
        (commit_sha, commit_sha, commit_sha),
        duplicate_session_id="XNYS-2026-07-20",
    )
    request = UsDayAcceptanceBuildRequest(repository=repository, terminal_paths=terminal_paths, generated_at=AT)

    # When / Then: the writer rejects the duplicate immutable session binding.
    with pytest.raises(InvalidUsDayAcceptanceEvidenceError):
        _ = write_us_day_acceptance_evidence(request)


def test_evidence_writer_rejects_invalid_terminal_commit_shape(tmp_path: Path) -> None:
    # Given: a terminal JSON file whose commit field has been malformed.
    repository = _clean_repository(tmp_path)
    commit_sha = _git(repository, "rev-parse", "HEAD")
    terminal_paths = _write_terminals(repository, (commit_sha, commit_sha, commit_sha))
    payload = json.loads((repository / terminal_paths[0]).read_text(encoding="utf-8"))
    payload["commit_sha"] = "invalid"
    (repository / terminal_paths[0]).write_text(json.dumps(payload), encoding="utf-8")
    request = UsDayAcceptanceBuildRequest(repository=repository, terminal_paths=terminal_paths, generated_at=AT)

    # When / Then: boundary parsing rejects a non-40-hex deployment commit.
    with pytest.raises(InvalidUsDayAcceptanceEvidenceError):
        _ = write_us_day_acceptance_evidence(request)


def test_evidence_writer_rejects_tampered_terminal_source(tmp_path: Path) -> None:
    # Given: terminal evidence whose recorded source digest was subsequently invalidated.
    repository = _clean_repository(tmp_path)
    commit_sha = _git(repository, "rev-parse", "HEAD")
    terminal_paths = _write_terminals(repository, (commit_sha, commit_sha, commit_sha))
    (repository / "outputs/source/session-0.json").write_text("tampered", encoding="utf-8")
    request = UsDayAcceptanceBuildRequest(repository=repository, terminal_paths=terminal_paths, generated_at=AT)

    # When / Then: source-hash verification prevents bundle emission.
    with pytest.raises(InvalidUsDayAcceptanceEvidenceError):
        _ = write_us_day_acceptance_evidence(request)


def _write_terminals(
    repository: Path,
    commit_shas: tuple[str, ...],
    *,
    duplicate_session_id: str | None = None,
) -> tuple[Path, ...]:
    terminal_paths: list[Path] = []
    for index, commit_sha in enumerate(commit_shas):
        relative = Path(f"outputs/acceptance/us_day/sessions/session-{index}.json")
        updates: dict[str, str] = {"commit_sha": commit_sha}
        if duplicate_session_id is not None and index == 2:
            updates["session_id"] = duplicate_session_id
        terminal = _terminal(index, natural=index == 0).model_copy(update=updates)
        source = repository / terminal.source_artifacts[0].path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source-{index}", encoding="utf-8")
        write_private_stable_report(repository / relative, terminal.model_dump_json(indent=2) + "\n")
        terminal_paths.append(relative)
    return tuple(terminal_paths)


def _terminal(index: int, *, natural: bool) -> UsDaySessionTerminal:
    session_date = dt.date(2026, 7, 20) + dt.timedelta(days=index)
    transitions = (
        (
            UsDayOperatingTransition.ACTIONABLE,
            UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
            UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
            UsDayOperatingTransition.FLAT,
            UsDayOperatingTransition.RECONCILED,
            UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
        )
        if natural
        else (
            UsDayOperatingTransition.FLAT,
            UsDayOperatingTransition.RECONCILED,
            UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
        )
    )
    return UsDaySessionTerminal(
        commit_sha="a" * 40,
        session_id=f"XNYS-{session_date.isoformat()}",
        strategy_version="orb-v1",
        session_kind=AcceptanceSessionKind.REAL,
        fixture_label="real_session",
        status=UsDayTerminalStatus.COMPLETED if natural else UsDayTerminalStatus.CENSORED,
        reasons=() if natural else ("censored_no_setup",),
        observed_from=AT + dt.timedelta(days=index),
        observed_through=AT + dt.timedelta(days=index, hours=6, minutes=30),
        transitions=transitions,
        open_order_count=0,
        position_count=0,
        protective_oco_count=0,
        reconciliation_passed=True,
        broker_shadow_ledger_equal=True,
        outcome_delivery_id="b" * 64,
        hermes_acknowledged=True,
        source_artifacts=(
            AcceptanceArtifactEvidence(
                path=Path(f"outputs/source/session-{index}.json"),
                sha256=hashlib.sha256(f"source-{index}".encode()).hexdigest(),
            ),
        ),
    )


def _clean_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _ = _git(repository, "init")
    _ = _git(repository, "config", "user.email", "qa@example.invalid")
    _ = _git(repository, "config", "user.name", "QA")
    (repository / ".gitignore").write_text("outputs/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _ = _git(repository, "add", ".")
    _ = _git(repository, "commit", "-m", "fixture")
    return repository


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
