from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tests.test_futures_roll_security_master_cli import (
    ManifestPayload,
    _manifest,
)
from trading_agent.futures_roll_security_master import (
    FuturesRollSecurityMasterError,
    load_futures_roll_security_master,
    resolve_active_futures_contract,
)


def test_provider_alias_must_cover_the_contract_active_window(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    first = payload["contracts"][0]
    provider_alias = first["provider_alias"]
    provider_alias["effective_to"] = "2026-08-01T00:00:00-05:00"
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_instrument_identity_must_cover_the_contract_active_window(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    first = payload["contracts"][0]
    instrument = first["instrument"]
    instrument["valid_to"] = "2026-08-01T00:00:00-05:00"
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_contract_cannot_become_active_before_listing(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    first = payload["contracts"][0]
    first["active_from"] = "2026-02-01T00:00:00-06:00"
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_contracts_must_share_one_provider_namespace(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    second = payload["contracts"][1]
    provider_alias = second["provider_alias"]
    provider_alias["namespace"] = "other"
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_contract_windows_must_be_continuous(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    second = payload["contracts"][1]
    second["active_from"] = "2026-09-11T16:00:00-05:00"
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_contract_cannot_be_known_before_it_is_listed(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    second = payload["contracts"][1]
    listed_at = "2026-07-01T00:00:00-05:00"
    second["listed_at"] = listed_at
    second["instrument"]["valid_from"] = listed_at
    second["provider_alias"]["effective_from"] = listed_at
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_physical_contract_rolls_before_first_notice(
    tmp_path: Path,
) -> None:
    # Given
    payload = _manifest()
    first = payload["contracts"][0]
    first["settlement_type"] = "physical"
    first["first_notice_at"] = "2026-09-01T08:30:00-05:00"
    manifest = _write_manifest(tmp_path, payload)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_manifest_must_be_private(
    tmp_path: Path,
) -> None:
    # Given
    manifest = _write_manifest(tmp_path, _manifest())
    manifest.chmod(0o644)

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = load_futures_roll_security_master(manifest)


def test_active_contract_cannot_precede_source_observation(
    tmp_path: Path,
) -> None:
    # Given
    master = load_futures_roll_security_master(
        _write_manifest(tmp_path, _manifest()),
    )
    as_of = dt.datetime.fromisoformat("2026-05-31T12:00:00-05:00")

    # When/Then
    with pytest.raises(FuturesRollSecurityMasterError):
        _ = resolve_active_futures_contract(master, as_of)


def _write_manifest(
    root: Path,
    payload: ManifestPayload,
) -> Path:
    path = root / "futures-roll.json"
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path
