from __future__ import annotations

import os
from pathlib import Path

import pytest

import run_kr_theme_day_open_smoke_verify as smoke_cli
from tests.kr_theme_day_open_smoke_support import VERIFIED_AT, production_session
from trading_agent import private_immutable_alias, private_immutable_file
from trading_agent.kr_theme_day_onboarding_models import onboarding_receipt_path
from trading_agent.kr_theme_day_open_smoke import load_kr_theme_day_open_smoke
from trading_agent.private_immutable_file import publish_private_immutable_text


def test_open_smoke_cli_blocks_symlink_loop_without_path_disclosure(tmp_path: Path) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    first = tmp_path / "evidence-loop-a"
    second = tmp_path / "evidence-loop-b"
    first.symlink_to(second)
    second.symlink_to(first)
    report_dir = tmp_path / "report"

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", first, report_dir),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    report = (report_dir / smoke_cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: blocked" in report
    assert str(first) not in report


def test_open_smoke_cli_preserves_manifest_when_loop_report_path_aliases_it(tmp_path: Path) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    manifest_path = report_dir / smoke_cli.REPORT_NAME
    original_manifest = tmp_path / "session.json"
    original_manifest.rename(manifest_path)
    onboarding_receipt_path(original_manifest).rename(onboarding_receipt_path(manifest_path))
    original = manifest_path.read_bytes()
    first = tmp_path / "evidence-loop-a"
    second = tmp_path / "evidence-loop-b"
    first.symlink_to(second)
    second.symlink_to(first)

    # When
    result = smoke_cli.main(
        _args(manifest_path, first, report_dir),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert manifest_path.read_bytes() == original


def test_open_smoke_cli_removes_final_when_publisher_commits_then_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"

    def reject_post_link_directory(_path: Path, _descriptor: int) -> None:
        raise OSError

    monkeypatch.setattr(
        private_immutable_alias,
        "require_open_directory_path",
        reject_post_link_directory,
    )

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()


def test_open_smoke_cli_preserves_foreign_final_when_current_publisher_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"
    foreign_payload = '{"owner":"foreign"}\n'

    def foreign_then_raise(_source: Path, target: Path) -> bool:
        assert publish_private_immutable_text(target, foreign_payload) is True
        raise OSError

    monkeypatch.setattr(
        smoke_cli,
        "publish_private_immutable_alias",
        foreign_then_raise,
        raising=False,
    )

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert destination.read_text(encoding="utf-8") == foreign_payload


def test_open_smoke_cli_replay_does_not_open_evidence_publication_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"
    args = _args(tmp_path / "session.json", destination, tmp_path / "report")
    assert smoke_cli.main(args, clock=lambda: VERIFIED_AT) == 0
    lock_path = destination.with_name(f".{destination.name}.publication.lock")
    lock_path.unlink(missing_ok=True)

    def reject_lock(_parent_descriptor: int, _name: str) -> int:
        raise AssertionError("query-only replay must not open a publication lock")

    monkeypatch.setattr(private_immutable_file, "_lock_publication", reject_lock)

    # When
    result = smoke_cli.main(args, clock=lambda: VERIFIED_AT.replace(hour=16))

    # Then
    assert result == 0
    assert not lock_path.exists()


def test_open_smoke_cli_blocks_report_directory_swap_without_overwriting_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    original_manifest = tmp_path / "session.json"
    target_dir = tmp_path / "target"
    target_dir.mkdir(mode=0o700)
    protected_manifest = target_dir / smoke_cli.REPORT_NAME
    original_manifest.rename(protected_manifest)
    onboarding_receipt_path(original_manifest).rename(onboarding_receipt_path(protected_manifest))
    original = protected_manifest.read_bytes()
    report_dir = tmp_path / "report"
    report_dir.mkdir(mode=0o700)
    displaced = tmp_path / "displaced-report"
    original_writer = smoke_cli.write_private_report

    def swap_then_write(path: Path, content: str) -> None:
        report_dir.rename(displaced)
        report_dir.symlink_to(target_dir, target_is_directory=True)
        original_writer(path, content)

    monkeypatch.setattr(smoke_cli, "write_private_report", swap_then_write)

    # When
    result = smoke_cli.main(
        _args(protected_manifest, tmp_path / "evidence.json", report_dir),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert protected_manifest.read_bytes() == original


def test_open_smoke_cli_rejects_hard_linked_experiment_ledger(tmp_path: Path) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    ledger_alias = tmp_path / "experiment-ledger-alias.sqlite3"
    os.link(manifest.paths.experiment_ledger, ledger_alias)
    destination = tmp_path / "open-smoke.json"

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()


def test_open_smoke_cli_rejects_ledger_symlink_swap_after_onboarding_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    manifest, _, _, _ = production_session(tmp_path)
    ledger = manifest.paths.experiment_ledger
    displaced = ledger.with_name("displaced-experiment.sqlite3")
    original_require = smoke_cli.require_exact_kr_theme_day_onboarding

    def verify_then_swap(path: Path, current: smoke_cli.KrThemeDaySessionManifest) -> None:
        original_require(path, current)
        ledger.rename(displaced)
        ledger.symlink_to(displaced)

    monkeypatch.setattr(smoke_cli, "require_exact_kr_theme_day_onboarding", verify_then_swap)
    destination = tmp_path / "open-smoke.json"

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert not destination.exists()


def test_open_smoke_cli_preserves_foreign_same_inode_final_on_publish_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    destination = tmp_path / "open-smoke.json"
    foreign_alias = tmp_path / "foreign-same-inode.json"

    def replace_then_raise(_path: Path, _descriptor: int) -> None:
        os.link(destination, foreign_alias)
        destination.unlink()
        os.link(foreign_alias, destination)
        foreign_alias.unlink()
        raise OSError

    monkeypatch.setattr(
        private_immutable_alias,
        "require_open_directory_path",
        replace_then_raise,
    )

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert load_kr_theme_day_open_smoke(destination).verified_at == VERIFIED_AT


def test_immutable_alias_does_not_sync_parent_after_commit_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "pending.json"
    destination = tmp_path / "final.json"
    source.write_text("evidence\n", encoding="utf-8")
    source.chmod(0o600)
    original_fsync = private_immutable_alias.os.fsync
    failed = False

    def fail_after_source_unlink(descriptor: int) -> None:
        nonlocal failed
        if not failed and not source.exists() and destination.exists():
            failed = True
            raise OSError
        original_fsync(descriptor)

    monkeypatch.setattr(private_immutable_alias.os, "fsync", fail_after_source_unlink)

    # When
    created = private_immutable_alias.publish_private_immutable_alias(source, destination)

    # Then
    assert created is True
    assert failed is False
    assert not source.exists()
    assert destination.stat().st_nlink == 1


def test_immutable_alias_does_not_refresh_state_after_commit_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "pending.json"
    destination = tmp_path / "final.json"
    source.write_text("evidence\n", encoding="utf-8")
    source.chmod(0o600)
    original_file_state = private_immutable_alias._file_state
    failed = False

    def fail_after_source_unlink(descriptor: int) -> tuple[int, int, int, int, int, int]:
        nonlocal failed
        if not failed and not source.exists() and destination.exists():
            failed = True
            raise OSError
        return original_file_state(descriptor)

    monkeypatch.setattr(private_immutable_alias, "_file_state", fail_after_source_unlink)

    # When
    created = private_immutable_alias.publish_private_immutable_alias(source, destination)

    # Then
    assert created is True
    assert failed is False
    assert not source.exists()
    assert destination.stat().st_nlink == 1


def test_immutable_alias_leaves_invalid_two_link_final_when_precommit_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "pending.json"
    destination = tmp_path / "final.json"
    source.write_text("evidence\n", encoding="utf-8")
    source.chmod(0o600)
    original_unlink = private_immutable_alias.os.unlink
    path_check_failed = False
    cleanup_unlink_failed = False

    def fail_path_check(_path: Path, _descriptor: int) -> None:
        nonlocal path_check_failed
        path_check_failed = True
        raise OSError

    def fail_final_cleanup(name: str, *, dir_fd: int | None = None) -> None:
        nonlocal cleanup_unlink_failed
        if name == destination.name and source.exists():
            cleanup_unlink_failed = True
            raise OSError
        original_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(private_immutable_alias, "require_open_directory_path", fail_path_check)
    monkeypatch.setattr(private_immutable_alias.os, "unlink", fail_final_cleanup)

    # When
    with pytest.raises(private_immutable_alias.InvalidPrivateImmutableAliasError):
        private_immutable_alias.publish_private_immutable_alias(source, destination)

    # Then
    assert path_check_failed is True
    assert cleanup_unlink_failed is True
    assert source.stat().st_nlink == 2
    assert destination.stat().st_nlink == 2


def test_immutable_alias_makes_source_unlink_the_last_filesystem_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "pending.json"
    destination = tmp_path / "final.json"
    source.write_text("evidence\n", encoding="utf-8")
    source.chmod(0o600)
    original_file_state = private_immutable_alias._file_state
    original_fsync = private_immutable_alias.os.fsync
    original_require_path = private_immutable_alias.require_open_directory_path
    post_unlink_calls: list[str] = []

    def file_state(descriptor: int) -> tuple[int, int, int, int, int, int]:
        if not source.exists():
            post_unlink_calls.append("file_state")
            raise OSError
        return original_file_state(descriptor)

    def fsync(descriptor: int) -> None:
        if not source.exists():
            post_unlink_calls.append("fsync")
            raise OSError
        original_fsync(descriptor)

    def require_path(path: Path, descriptor: int) -> None:
        if not source.exists():
            post_unlink_calls.append("require_path")
            raise OSError
        original_require_path(path, descriptor)

    monkeypatch.setattr(private_immutable_alias, "_file_state", file_state)
    monkeypatch.setattr(private_immutable_alias.os, "fsync", fsync)
    monkeypatch.setattr(private_immutable_alias, "require_open_directory_path", require_path)

    # When
    created = private_immutable_alias.publish_private_immutable_alias(source, destination)

    # Then
    assert created is True
    assert post_unlink_calls == []
    assert not source.exists()
    assert destination.stat().st_nlink == 1


def test_open_smoke_cli_cleanup_preserves_foreign_file_after_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _manifest, _, _, _ = production_session(tmp_path)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(mode=0o700)
    destination = evidence_dir / "open-smoke.json"
    owned_dir = tmp_path / "owned-evidence"
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir(mode=0o700)
    foreign = foreign_dir / destination.name
    foreign.write_text("foreign\n", encoding="utf-8")
    foreign.chmod(0o600)

    def swap_then_fail(_path: Path, _content: str) -> None:
        evidence_dir.rename(owned_dir)
        evidence_dir.symlink_to(foreign_dir, target_is_directory=True)
        raise OSError

    monkeypatch.setattr(smoke_cli, "write_private_report", swap_then_fail)

    # When
    result = smoke_cli.main(
        _args(tmp_path / "session.json", destination, tmp_path / "report"),
        clock=lambda: VERIFIED_AT,
    )

    # Then
    assert result == 1
    assert foreign.read_text(encoding="utf-8") == "foreign\n"
    assert (owned_dir / destination.name).exists()


def _args(manifest: Path, evidence: Path, output_dir: Path) -> tuple[str, ...]:
    return (
        "--manifest",
        str(manifest),
        "--evidence",
        str(evidence),
        "--output-dir",
        str(output_dir),
    )
