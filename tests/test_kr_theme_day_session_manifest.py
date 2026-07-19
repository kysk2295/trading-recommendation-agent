from __future__ import annotations

import datetime as dt
import hashlib
import json
import stat
from pathlib import Path

import pytest

import trading_agent.private_immutable_file as private_file
from tests.test_kis_kr_market_projection import _opportunity
from trading_agent.kr_theme_day_session_manifest import (
    InvalidKrThemeDaySessionManifestError,
    KrThemeDaySessionIdentity,
    KrThemeDaySessionPaths,
    build_kr_theme_day_session_manifest,
    load_kr_theme_day_session_manifest,
    write_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_research_registration import kr_theme_strategy_version


def _identity(tmp_path: Path) -> KrThemeDaySessionIdentity:
    return KrThemeDaySessionIdentity(
        strategy_version="kr-theme-v1",
        code_version="code-v1",
        session_date=dt.date(2026, 7, 20),
        registered_at=dt.datetime(2026, 7, 19, 8, 31, tzinfo=dt.timezone(dt.timedelta(hours=9))),
        calendar_snapshot_id="a" * 64,
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        opportunity_strategy_version=kr_theme_strategy_version("kr-theme-fixture-code-v1"),
        opportunity_sha256=_opportunity_sha256(),
        symbol="005930",
        paths=KrThemeDaySessionPaths(
            experiment_ledger=tmp_path / "experiment.sqlite3",
            calendar_store=tmp_path / "calendar.sqlite3",
            opportunity_outbox=tmp_path / "opportunities.jsonl",
            receipt_store=tmp_path / "receipts.sqlite3",
            entry_store=tmp_path / "entries.sqlite3",
            exit_store=tmp_path / "exits.sqlite3",
            terminal_store=tmp_path / "terminals.sqlite3",
            review_store=tmp_path / "reviews.sqlite3",
            audit_store=tmp_path / "session-audit.sqlite3",
            output_root=tmp_path / "reports",
        ),
    )


def _opportunity_sha256() -> str:
    payload = json.dumps(
        _opportunity().model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_manifest_round_trip_is_content_addressed_and_private(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "session.json"
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))

    # When
    write_kr_theme_day_session_manifest(path, manifest)
    loaded = load_kr_theme_day_session_manifest(path)

    # Then
    assert loaded == manifest
    assert len(loaded.session_id) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_manifest_reader_rejects_tamper_and_non_private_mode(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "session.json"
    write_kr_theme_day_session_manifest(path, build_kr_theme_day_session_manifest(_identity(tmp_path)))
    original = path.read_text(encoding="utf-8")

    # When / Then
    path.write_text(original.replace("005930", "000660"), encoding="utf-8")
    with pytest.raises(InvalidKrThemeDaySessionManifestError):
        _ = load_kr_theme_day_session_manifest(path)
    path.write_text(original, encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(InvalidKrThemeDaySessionManifestError):
        _ = load_kr_theme_day_session_manifest(path)


def test_manifest_interrupted_write_leaves_no_final_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    path = tmp_path / "session.json"
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    original = private_file.os.fdopen

    def interrupt(_descriptor: int, _mode: str, *, encoding: str) -> None:
        del encoding
        raise OSError("fixture write interruption")

    monkeypatch.setattr(private_file.os, "fdopen", interrupt)

    # When
    with pytest.raises(InvalidKrThemeDaySessionManifestError):
        write_kr_theme_day_session_manifest(path, manifest)
    monkeypatch.setattr(private_file.os, "fdopen", original)
    write_kr_theme_day_session_manifest(path, manifest)

    # Then
    assert load_kr_theme_day_session_manifest(path) == manifest


def test_manifest_rejects_symlinked_parent(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "target"
    target.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(target, target_is_directory=True)
    path = linked_parent / "session.json"

    # When / Then
    with pytest.raises(InvalidKrThemeDaySessionManifestError):
        write_kr_theme_day_session_manifest(path, build_kr_theme_day_session_manifest(_identity(tmp_path)))
    assert not (target / "session.json").exists()
    direct = target / "session.json"
    write_kr_theme_day_session_manifest(direct, build_kr_theme_day_session_manifest(_identity(tmp_path)))
    with pytest.raises(InvalidKrThemeDaySessionManifestError):
        _ = load_kr_theme_day_session_manifest(path)


def test_manifest_reader_keeps_opened_file_during_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    path = tmp_path / "session.json"
    manifest = build_kr_theme_day_session_manifest(_identity(tmp_path))
    write_kr_theme_day_session_manifest(path, manifest)
    replacement = tmp_path / "replacement.json"
    replacement_manifest = build_kr_theme_day_session_manifest(
        _identity(tmp_path).model_copy(update={"opportunity_id": "KR-THEME-OPPORTUNITY-002"})
    )
    write_kr_theme_day_session_manifest(replacement, replacement_manifest)
    replacement.chmod(0o644)
    original_open = private_file.os.open
    swapped = False

    def swap_after_open(
        target: str | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(target, flags, mode, dir_fd=dir_fd)
        if target == path.name and dir_fd is not None and not swapped:
            path.unlink()
            path.symlink_to(replacement)
            swapped = True
        return descriptor

    monkeypatch.setattr(private_file.os, "open", swap_after_open)

    # When / Then
    with pytest.raises(InvalidKrThemeDaySessionManifestError):
        _ = load_kr_theme_day_session_manifest(path)
    assert swapped is True
