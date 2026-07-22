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
    AcceptanceEvidenceBuildRequest,
    AcceptanceEvidenceFailure,
    AcceptanceEvidenceManifest,
    AcceptanceSessionEvidence,
    AcceptanceSessionKind,
    InvalidAcceptanceEvidenceError,
    build_acceptance_manifest,
    main,
    verify_acceptance_manifest,
)

UTC = dt.UTC
AT = dt.datetime(2026, 7, 22, 14, 0, tzinfo=UTC)


def test_operational_manifest_rejects_fixture_session(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest = _manifest(repository, session_kind=AcceptanceSessionKind.FIXTURE)

    # When / Then
    with pytest.raises(InvalidAcceptanceEvidenceError) as captured:
        verify_acceptance_manifest(
            manifest,
            repository,
            require_clean_commit=True,
            require_session_binding=True,
        )
    assert captured.value.reason is AcceptanceEvidenceFailure.NON_REAL_SESSION


def test_operational_manifest_detects_artifact_tamper(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest = _manifest(repository)
    (repository / "evidence.json").write_text('{"changed":true}\n', encoding="utf-8")

    # When / Then
    with pytest.raises(InvalidAcceptanceEvidenceError) as captured:
        verify_acceptance_manifest(
            manifest,
            repository,
            require_clean_commit=False,
            require_session_binding=True,
        )
    assert captured.value.reason is AcceptanceEvidenceFailure.ARTIFACT_HASH_MISMATCH


def test_operational_manifest_rejects_wrong_commit(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest = _manifest(repository).model_copy(update={"commit_sha": "0" * 40})

    # When / Then
    with pytest.raises(InvalidAcceptanceEvidenceError) as captured:
        verify_acceptance_manifest(
            manifest,
            repository,
            require_clean_commit=True,
            require_session_binding=True,
        )
    assert captured.value.reason is AcceptanceEvidenceFailure.COMMIT_MISMATCH


def test_operational_manifest_rejects_dirty_repository(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest = _manifest(repository)
    (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    # When / Then
    with pytest.raises(InvalidAcceptanceEvidenceError) as captured:
        verify_acceptance_manifest(
            manifest,
            repository,
            require_clean_commit=True,
            require_session_binding=True,
        )
    assert captured.value.reason is AcceptanceEvidenceFailure.DIRTY_REPOSITORY


def test_operational_manifest_requires_at_least_one_session(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest = _manifest(repository).model_copy(update={"sessions": ()})

    # When / Then
    with pytest.raises(InvalidAcceptanceEvidenceError) as captured:
        verify_acceptance_manifest(
            manifest,
            repository,
            require_clean_commit=True,
            require_session_binding=True,
        )
    assert captured.value.reason is AcceptanceEvidenceFailure.MISSING_SESSION


def test_operational_manifest_verifies_exact_clean_commit_and_real_session(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest = _manifest(repository)

    # When
    verify_acceptance_manifest(
        manifest,
        repository,
        require_clean_commit=True,
        require_session_binding=True,
    )

    # Then
    assert manifest.commit_sha == _git(repository, "rev-parse", "HEAD")


def test_build_manifest_hashes_artifacts_and_writes_mode_600_json(tmp_path: Path) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    output = tmp_path / "private" / "manifest.json"
    request = AcceptanceEvidenceBuildRequest(
        criterion_id="AC-001",
        policy_version="policy-v1",
        verifier_version="acceptance-evidence-v1",
        generated_at=AT,
        sessions=(_session(),),
        artifact_paths=(Path("evidence.json"),),
    )

    # When
    manifest = build_acceptance_manifest(request, repository, output)

    # Then
    assert manifest.commit_sha == _git(repository, "rev-parse", "HEAD")
    assert manifest.artifacts[0].sha256 == _sha256(repository / "evidence.json")
    assert AcceptanceEvidenceManifest.model_validate_json(output.read_text(encoding="utf-8")) == manifest
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_verify_cli_accepts_seed_command_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(_manifest(repository).model_dump_json(), encoding="utf-8")

    # When
    exit_code = main(
        (
            "verify",
            "--criterion",
            "AC-001",
            "--manifest",
            str(manifest_path),
            "--repository",
            str(repository),
            "--require-clean-commit",
            "--require-session-binding",
        )
    )

    # Then
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"criterion_id": "AC-001", "result": "verified"}


def test_verify_cli_rejects_criterion_mismatch_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    repository = _clean_repository(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(_manifest(repository).model_dump_json(), encoding="utf-8")

    # When
    exit_code = main(
        (
            "verify",
            "--criterion",
            "AC-002",
            "--manifest",
            str(manifest_path),
            "--repository",
            str(repository),
            "--require-clean-commit",
            "--require-session-binding",
        )
    )

    # Then
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"reason": "criterion_mismatch", "result": "blocked"}


def _clean_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _ = _git(repository, "init")
    _ = _git(repository, "config", "user.email", "qa@example.invalid")
    _ = _git(repository, "config", "user.name", "QA")
    (repository / ".gitignore").write_text("outputs/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    (repository / "evidence.json").write_text('{"result":"observed"}\n', encoding="utf-8")
    _ = _git(repository, "add", ".")
    _ = _git(repository, "commit", "-m", "fixture")
    return repository


def _manifest(
    repository: Path,
    *,
    session_kind: AcceptanceSessionKind = AcceptanceSessionKind.REAL,
) -> AcceptanceEvidenceManifest:
    return AcceptanceEvidenceManifest(
        criterion_id="AC-001",
        policy_version="policy-v1",
        commit_sha=_git(repository, "rev-parse", "HEAD"),
        verifier_version="acceptance-evidence-v1",
        generated_at=AT,
        sessions=(_session(kind=session_kind),),
        artifacts=(
            AcceptanceArtifactEvidence(
                path=Path("evidence.json"),
                sha256=_sha256(repository / "evidence.json"),
            ),
        ),
    )


def _session(
    *,
    kind: AcceptanceSessionKind = AcceptanceSessionKind.REAL,
) -> AcceptanceSessionEvidence:
    return AcceptanceSessionEvidence(
        session_id="XNYS-2026-07-22",
        market_id="us_equities",
        kind=kind,
        observed_from=AT,
        observed_through=AT + dt.timedelta(hours=6, minutes=30),
    )


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
