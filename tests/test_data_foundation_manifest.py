from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from trading_agent.data_foundation_manifest import (
    DataFoundationManifest,
    InvalidDataFoundationManifestError,
    load_data_foundation_artifact,
    load_data_foundation_manifest,
)
from trading_agent.strategy_data_gate import StrategyDataStatus

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "data" / "us-orb-data-foundation-v1.json"


def test_fixture_manifest_cross_links_and_evaluates_ready() -> None:
    manifest = load_data_foundation_manifest(EXAMPLE)
    decision = manifest.evaluate_data_readiness()

    assert manifest.manifest_id == "us-orb-data-foundation-v1"
    assert manifest.strategy_lane.canonical_id == "us_equities/day_trading/orb"
    assert decision.status is StrategyDataStatus.READY
    assert decision.evaluations[0].selected_source_id is not None
    assert decision.evaluations[0].selected_source_id.canonical_id == "fixture/sip"


def test_foundation_artifact_hashes_the_exact_validated_bytes() -> None:
    artifact = load_data_foundation_artifact(EXAMPLE)

    assert artifact.manifest == load_data_foundation_manifest(EXAMPLE)
    assert len(artifact.sha256) == 64


def test_loader_rejects_missing_or_non_file_manifest(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataFoundationManifestError, match="data foundation manifest 계약이 유효하지 않습니다"):
        load_data_foundation_manifest(tmp_path / "missing.json")
    with pytest.raises(InvalidDataFoundationManifestError):
        load_data_foundation_manifest(tmp_path)


def test_manifest_requires_one_capability_and_entitlement_per_source() -> None:
    duplicate_capability = _payload()
    duplicate_capability["capabilities"].append(copy.deepcopy(duplicate_capability["capabilities"][0]))
    mismatched_entitlement = _payload()
    mismatched_entitlement["entitlements"][0]["source_id"]["provider"] = "other"

    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(duplicate_capability)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(mismatched_entitlement)


def test_requirements_reference_only_manifest_lane_and_declared_sources() -> None:
    wrong_lane = _payload()
    wrong_lane["requirements"][0]["strategy_lane"]["strategy_id"] = "gap_and_go"
    undeclared_fallback = _payload()
    undeclared_fallback["requirements"][0]["fallback_source_ids"] = [
        {"schema_version": 1, "provider": "other", "feed": "sip"}
    ]

    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(wrong_lane)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(undeclared_fallback)


def test_aliases_and_actions_reference_declared_instruments() -> None:
    unknown_alias = _payload()
    unknown_alias["aliases"][0]["instrument_id"] = "us-eq-unknown-0001"
    unknown_action = _payload()
    unknown_action["corporate_actions"] = [
        {
            "schema_version": 1,
            "action_id": "fixture-delisting-0001",
            "action_type": "delisting",
            "instrument_id": "us-eq-unknown-0001",
            "announced_at": "2026-07-01T00:00:00Z",
            "effective_at": "2026-07-10T00:00:00Z",
            "ratio_numerator": None,
            "ratio_denominator": None,
            "cash_amount": None,
            "currency": None,
            "successor_instrument_id": None,
        }
    ]

    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(unknown_alias)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(unknown_action)


def test_overlapping_alias_intervals_are_rejected() -> None:
    payload = _payload()
    overlap = copy.deepcopy(payload["aliases"][0])
    overlap["effective_from"] = "2026-01-01T00:00:00Z"
    payload["aliases"].append(overlap)

    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(payload)


def test_events_reference_declared_source_instrument_and_event_type() -> None:
    unknown_source = _payload()
    unknown_source["events"][0]["source_id"]["provider"] = "other"
    unknown_instrument = _payload()
    unknown_instrument["events"][0]["entity_refs"][0]["entity_id"] = "us-eq-unknown-0001"
    unsupported_type = _payload()
    unsupported_type["events"][0]["event_type"] = "option_quote"

    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(unknown_source)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(unknown_instrument)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(unsupported_type)


def test_manifest_rejects_noncanonical_order_and_future_normalization() -> None:
    duplicate_event = _payload()
    duplicate_event["events"].append(copy.deepcopy(duplicate_event["events"][0]))
    future_event = _payload()
    future_event["events"][0]["normalized_at"] = "2026-07-17T14:00:00.000001Z"
    reverse_times = _payload()
    reverse_times["registered_at"] = "2026-07-17T14:00:01Z"

    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(duplicate_event)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(future_event)
    with pytest.raises(ValidationError):
        DataFoundationManifest.model_validate(reverse_times)


def _payload() -> dict[str, Any]:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
