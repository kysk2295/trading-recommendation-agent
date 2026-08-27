from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

import pytest

import run_research_agent_runtime as service_cli
import trading_agent.research_agent_service_v4_verification as v4_verification
from run_research_agent_runtime import main
from tests.research_agent_browser_service_fixtures import browser_service_config
from tests.test_kis_kr_session_calendar import _receipt
from tests.test_kr_social_signal_store import _signal
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_agent_service_config import (
    InvalidResearchAgentServiceConfigError,
    ResearchAgentServiceConfig,
    write_research_agent_launch_agent,
    write_research_agent_service_config,
)

NOW = dt.datetime(2026, 7, 20, 6, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "bad_binding",
    (
        "plist_name",
        "missing_browser",
        "missing_market",
        "public_market",
        "symlink_market",
        "missing_social",
        "public_social",
        "symlink_social",
        "tampered_social",
        "missing_calendar",
        "misdirected_calendar",
        "public_calendar",
        "symlink_calendar",
        "tampered_calendar",
        "swap_calendar",
        "mutate_calendar",
        "missing_kis",
        "public_kis",
        "symlink_kis",
        "hardlink_kis",
        "swap_kis",
        "mutate_kis",
    ),
)
def test_v4_activate_rejects_bad_binding_before_launchctl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_binding: str,
) -> None:
    # Given: a fully pre-provisioned v4 service and one invalid authority binding.
    config, config_path, plist_path = _provision_v4(tmp_path, monkeypatch, bad_binding)
    if bad_binding == "plist_name":
        wrong = plist_path.with_name("unversioned.plist")
        assert publish_private_immutable_text(wrong, plist_path.read_text(encoding="utf-8"))
        plist_path = wrong
    elif bad_binding == "missing_browser":
        assert config.browser_gateway_config is not None
        config.browser_gateway_config.unlink()
    elif bad_binding == "missing_market":
        assert config.kr_market_receipt_root is not None
        config.kr_market_receipt_root.rmdir()
    elif bad_binding == "public_market":
        assert config.kr_market_receipt_root is not None
        config.kr_market_receipt_root.chmod(0o755)
    elif bad_binding == "symlink_market":
        assert config.kr_market_receipt_root is not None
        config.kr_market_receipt_root.rmdir()
        target = tmp_path / "market-target"
        target.mkdir(mode=0o700)
        config.kr_market_receipt_root.symlink_to(target)
    elif bad_binding == "missing_social":
        assert config.kr_social_signal_database is not None
        config.kr_social_signal_database.unlink()
    elif bad_binding == "public_social":
        assert config.kr_social_signal_database is not None
        config.kr_social_signal_database.chmod(0o640)
    elif bad_binding == "symlink_social":
        assert config.kr_social_signal_database is not None
        target = tmp_path / "social-target.sqlite3"
        config.kr_social_signal_database.rename(target)
        config.kr_social_signal_database.symlink_to(target)
    elif bad_binding == "tampered_social":
        assert config.kr_social_signal_database is not None
        with sqlite3.connect(config.kr_social_signal_database) as connection:
            connection.execute("DROP TRIGGER kr_social_signals_no_delete")
    elif bad_binding == "missing_calendar":
        assert config.source_paths.kr_calendar_store is not None
        config.source_paths.kr_calendar_store.unlink()
    elif bad_binding == "public_calendar":
        assert config.source_paths.kr_calendar_store is not None
        config.source_paths.kr_calendar_store.chmod(0o640)
    elif bad_binding == "symlink_calendar":
        assert config.source_paths.kr_calendar_store is not None
        target = tmp_path / "calendar-target.sqlite3"
        config.source_paths.kr_calendar_store.rename(target)
        config.source_paths.kr_calendar_store.symlink_to(target)
    elif bad_binding == "tampered_calendar":
        assert config.source_paths.kr_calendar_store is not None
        with sqlite3.connect(config.source_paths.kr_calendar_store) as connection:
            connection.execute("DROP TRIGGER kis_kr_session_calendars_no_update")
    elif bad_binding in {"swap_calendar", "mutate_calendar"}:
        assert config.source_paths.kr_calendar_store is not None
        _install_read_attack(monkeypatch, config.source_paths.kr_calendar_store, bad_binding)
    elif bad_binding == "missing_kis":
        v4_verification.KIS_SECRET_PATH.unlink()
    elif bad_binding == "public_kis":
        v4_verification.KIS_SECRET_PATH.chmod(0o640)
    elif bad_binding == "symlink_kis":
        target = tmp_path / "kis-target.env"
        v4_verification.KIS_SECRET_PATH.rename(target)
        v4_verification.KIS_SECRET_PATH.symlink_to(target)
    elif bad_binding == "hardlink_kis":
        target = tmp_path / "kis-target.env"
        v4_verification.KIS_SECRET_PATH.rename(target)
        os.link(target, v4_verification.KIS_SECRET_PATH)
    elif bad_binding in {"swap_kis", "mutate_kis"}:
        _install_read_attack(monkeypatch, v4_verification.KIS_SECRET_PATH, bad_binding)

    calls: list[tuple[str, ...]] = []

    # When: activation validates the candidate before invoking launchctl.
    result = main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda command: calls.append(command) or 0,
    )

    # Then: every invalid v4 binding fails closed with no service-manager mutation.
    assert result == 2
    assert calls == []


def test_v4_verify_accepts_all_exact_preprovisioned_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: every v4 browser, KR store, calendar, credential, and plist binding is exact.
    _, config_path, plist_path = _provision_v4(tmp_path, monkeypatch, "valid")

    # When/Then: the read-only verification path accepts the candidate.
    assert main(("verify", "--config", str(config_path), "--plist", str(plist_path))) == 0
    output = capsys.readouterr().out
    assert "fixture-key" not in output and "fixture-secret" not in output


def test_v4_launch_agent_requires_exact_versioned_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid v4 candidate bound to an unversioned plist destination.
    config, config_path, _ = _provision_v4(tmp_path, monkeypatch, "valid")
    wrong = tmp_path / "private" / "runtime.plist"

    # When/Then: publishing rejects the name before creating any file.
    with pytest.raises(InvalidResearchAgentServiceConfigError):
        _ = write_research_agent_launch_agent(wrong, config, config_path)
    assert not wrong.exists()


def _provision_v4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_binding: str,
) -> tuple[ResearchAgentServiceConfig, Path, Path]:
    source = browser_service_config(tmp_path)
    market_root = tmp_path / "market-receipts"
    market_root.mkdir(mode=0o700)
    social_database = tmp_path / "social-signals.sqlite3"
    assert KrSocialSignalStore(social_database).append(_signal())
    calendar_database = tmp_path / "calendar.sqlite3"
    receipt = _receipt()
    assert KisKrSessionCalendarStore(calendar_database).append(receipt, project_kis_kr_session_calendar(receipt))
    calendar_binding = social_database if bad_binding == "misdirected_calendar" else calendar_database
    source_paths = source.source_paths.model_copy(update={"kr_calendar_store": calendar_binding})
    config = ResearchAgentServiceConfig.model_validate(
        source.model_dump(mode="python")
        | {
            "schema_version": 4,
            "kr_market_receipt_root": market_root,
            "kr_social_signal_database": social_database,
            "source_paths": source_paths,
        }
    )
    monkeypatch.setattr(v4_verification, "utc_now", lambda: NOW)
    monkeypatch.setattr(service_cli, "current_main_commit", lambda project_root: "a" * 40)
    kis_secret = tmp_path / "kis.env"
    kis_secret.write_text("KIS_LIVE_APP_KEY=fixture-key\nKIS_LIVE_APP_SECRET=fixture-secret\n", encoding="utf-8")
    kis_secret.chmod(0o600)
    monkeypatch.setattr(v4_verification, "KIS_SECRET_PATH", kis_secret)
    config_path = tmp_path / "private" / "runtime.json"
    plist_path = tmp_path / "private" / v4_verification.V4_PLIST_FILENAME
    assert write_research_agent_service_config(config_path, config)
    assert write_research_agent_launch_agent(plist_path, config, config_path)
    return config, config_path, plist_path


def _install_read_attack(monkeypatch: pytest.MonkeyPatch, path: Path, attack: str) -> None:
    real_read = v4_verification.os.read
    target_identity = (path.stat().st_dev, path.stat().st_ino)
    original = path.read_bytes()
    attacked = False

    def adversarial_read(descriptor: int, count: int) -> bytes:
        nonlocal attacked
        chunk = real_read(descriptor, count)
        metadata = os.fstat(descriptor)
        if not attacked and (metadata.st_dev, metadata.st_ino) == target_identity:
            attacked = True
            if attack.startswith("swap"):
                held = path.with_name(f"{path.name}.held")
                replacement = path.with_name(f"{path.name}.replacement")
                replacement.write_bytes(original)
                replacement.chmod(0o600)
                path.rename(held)
                replacement.rename(path)
            else:
                with path.open("r+b") as stream:
                    _ = stream.write(bytes((original[0] ^ 1,)) + original[1:])
                    stream.flush()
                    os.fsync(stream.fileno())
        return chunk

    monkeypatch.setattr(v4_verification.os, "read", adversarial_read)
