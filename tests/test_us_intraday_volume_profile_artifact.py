from __future__ import annotations

import datetime as dt
import json
import os
import stat
from pathlib import Path

import pytest

from tests.us_volume_profile_fixtures import historical_volume_profile
from trading_agent.us_intraday_volume_profile_artifact import (
    IntradayVolumeProfileArtifactError,
    IntradayVolumeProfileArtifactStore,
)


def test_private_artifact_round_trip_is_idempotent(tmp_path: Path) -> None:
    profile = historical_volume_profile(
        "alpaca:asset-acme",
        dt.date(2026, 7, 17),
    )
    store = IntradayVolumeProfileArtifactStore(tmp_path / "profiles")

    first = store.append(profile)
    second = store.append(profile)

    assert first == second
    assert store.load(first) == profile
    assert stat.S_IMODE(os.stat(tmp_path / "profiles").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(first).st_mode) == 0o600
    assert first.name == f"profile_{profile.evidence_sha256}.json"


def test_tampered_source_identity_is_rejected(tmp_path: Path) -> None:
    profile = historical_volume_profile(
        "alpaca:asset-acme",
        dt.date(2026, 7, 17),
    )
    store = IntradayVolumeProfileArtifactStore(tmp_path / "profiles")
    path = store.append(profile)
    payload = json.loads(path.read_bytes())
    payload["source_identities"][0]["dataset_id"] = "tampered-dataset"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="ascii")
    path.chmod(0o600)

    with pytest.raises(IntradayVolumeProfileArtifactError, match="invalid"):
        store.load(path)


def test_symlinked_artifact_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "profiles"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(IntradayVolumeProfileArtifactError, match="invalid"):
        IntradayVolumeProfileArtifactStore(link)
