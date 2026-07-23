from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_futures_roll_security_master.py"


class InstrumentPayload(TypedDict):
    schema_version: int
    value: str
    market_domain: str
    asset_class: str
    venue: str
    currency: str
    timezone: str
    valid_from: str
    valid_to: str | None


class ProviderAliasPayload(TypedDict):
    schema_version: int
    instrument_id: str
    namespace: str
    alias_type: str
    value: str
    effective_from: str
    effective_to: str | None


class ContractPayload(TypedDict):
    instrument: InstrumentPayload
    provider_alias: ProviderAliasPayload
    root_symbol: str
    settlement_type: str
    multiplier: str
    listed_at: str
    active_from: str
    roll_at: str
    first_notice_at: str | None
    last_trade_at: str
    expiration_date: str
    observed_at: str


class ManifestPayload(TypedDict):
    schema_version: int
    root_symbol: str
    source_observed_at: str
    source_reference: str
    contracts: list[ContractPayload]


def test_private_manifest_publishes_content_addressed_roll_master(
    tmp_path: Path,
) -> None:
    # Given
    manifest = tmp_path / "futures-roll.json"
    manifest.write_text(
        json.dumps(_manifest(), separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    output = tmp_path / "output"

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--as-of",
            "2026-09-11T12:00:00-05:00",
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    artifacts = tuple(output.glob("futures_roll_security_master_*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["root_symbol"] == "ES"
    assert [item["provider_alias"]["value"] for item in payload["contracts"]] == [
        "ESU6",
        "ESZ6",
    ]
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    report = (output / "futures_roll_security_master_ko.md").read_text(encoding="utf-8")
    assert "- contract count: 2" in report
    assert "- active contract: present" in report
    assert "- network access: 0" in report
    assert "- broker, account, or order mutation: none" in report

    # When
    artifact_sha256 = hashlib.sha256(artifacts[0].read_bytes()).hexdigest()
    replay = subprocess.run(
        completed.args,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=no" in replay.stdout
    replayed_artifacts = tuple(output.glob("futures_roll_security_master_*.json"))
    assert len(replayed_artifacts) == 1
    assert hashlib.sha256(replayed_artifacts[0].read_bytes()).hexdigest() == artifact_sha256


def test_public_manifest_is_rejected_before_output(
    tmp_path: Path,
) -> None:
    # Given
    manifest = tmp_path / "futures-roll.json"
    manifest.write_text(
        json.dumps(_manifest(), separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    manifest.chmod(0o644)
    output = tmp_path / "output"

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--as-of",
            "2026-09-11T12:00:00-05:00",
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 2
    assert not output.exists()


def _manifest() -> ManifestPayload:
    observed = "2026-06-01T12:00:00-05:00"
    return {
        "schema_version": 1,
        "root_symbol": "ES",
        "source_observed_at": observed,
        "source_reference": "https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.html",
        "contracts": [
            _contract(
                "cme:es-202609",
                "ESU6",
                observed,
                "2026-09-10T16:00:00-05:00",
                "2026-09-18T08:30:00-05:00",
                "2026-09-18",
            ),
            _contract(
                "cme:es-202612",
                "ESZ6",
                "2026-09-10T16:00:00-05:00",
                "2026-12-10T16:00:00-06:00",
                "2026-12-18T08:30:00-06:00",
                "2026-12-18",
            ),
        ],
    }


def _contract(
    instrument_id: str,
    provider_symbol: str,
    active_from: str,
    roll_at: str,
    last_trade_at: str,
    expiration_date: str,
) -> ContractPayload:
    listed_at = "2026-03-01T00:00:00-06:00"
    return {
        "instrument": {
            "schema_version": 1,
            "value": instrument_id,
            "market_domain": "us_derivatives",
            "asset_class": "future",
            "venue": "XCME",
            "currency": "USD",
            "timezone": "America/Chicago",
            "valid_from": listed_at,
            "valid_to": None,
        },
        "provider_alias": {
            "schema_version": 1,
            "instrument_id": instrument_id,
            "namespace": "cme",
            "alias_type": "provider_symbol",
            "value": provider_symbol,
            "effective_from": listed_at,
            "effective_to": None,
        },
        "root_symbol": "ES",
        "settlement_type": "cash",
        "multiplier": "50",
        "listed_at": listed_at,
        "active_from": active_from,
        "roll_at": roll_at,
        "first_notice_at": None,
        "last_trade_at": last_trade_at,
        "expiration_date": expiration_date,
        "observed_at": "2026-06-01T12:00:00-05:00",
    }
