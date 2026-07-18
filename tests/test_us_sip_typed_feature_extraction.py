from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import cast

import httpx2
import pytest

from tests.alpaca_sip_runtime_fleet_fixtures import (
    decision,
    feature_requests,
    fleet,
    wire_bars,
)
from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.research_evidence_models import (
    ClaimCorroborationStatus,
    ClaimStance,
    ExtractionMethod,
)
from trading_agent.us_sip_research_evidence_projection import (
    UsSipResearchEvidenceProjectionError,
    project_us_sip_research_evidence,
)
from trading_agent.us_sip_typed_feature_extraction import (
    UsSipTypedFeatureExtractionError,
    extract_us_sip_typed_feature_claims,
)


def test_ready_snapshot_emits_exact_breakout_and_rvol_claims(tmp_path: Path) -> None:
    snapshot, dataset = _ready_snapshot_and_dataset(tmp_path)

    claims = extract_us_sip_typed_feature_claims(
        snapshot,
        dataset,
        minimum_rvol_bps=10_000,
    )

    assert tuple(item.claim_key for item in claims) == (
        "us.intraday.breakout.close_above_prior_high",
        "us.intraday.rvol.gte.10000bps",
    )
    assert tuple(item.stance for item in claims) == (
        ClaimStance.DISPUTES,
        ClaimStance.SUPPORTS,
    )
    assert all(item.source_id.canonical_id == "alpaca/sip" for item in claims)
    assert all(item.event_id == claims[0].event_id for item in claims)
    assert all(item.event_content_hash == claims[0].event_content_hash for item in claims)
    assert all(item.raw_receipt_ref == claims[0].raw_receipt_ref for item in claims)
    assert all(item.entity_refs == claims[0].entity_refs for item in claims)
    assert all(item.extracted_at == snapshot.observed_at for item in claims)
    assert all(item.extraction_method is ExtractionMethod.DETERMINISTIC for item in claims)
    assert all(item.model_version is None and item.prompt_version is None for item in claims)
    assert claims[0].output_sha256 != claims[1].output_sha256


def test_threshold_is_part_of_claim_identity_and_output_hash(tmp_path: Path) -> None:
    snapshot, dataset = _ready_snapshot_and_dataset(tmp_path)

    lower = extract_us_sip_typed_feature_claims(snapshot, dataset, minimum_rvol_bps=10_000)
    higher = extract_us_sip_typed_feature_claims(snapshot, dataset, minimum_rvol_bps=20_000)

    assert lower[1].claim_key == "us.intraday.rvol.gte.10000bps"
    assert higher[1].claim_key == "us.intraday.rvol.gte.20000bps"
    assert lower[1].output_sha256 != higher[1].output_sha256
    assert higher[1].stance is ClaimStance.DISPUTES


def test_wrong_dataset_blocked_snapshot_and_invalid_threshold_fail_closed(tmp_path: Path) -> None:
    snapshot, dataset = _ready_snapshot_and_dataset(tmp_path)
    other_snapshot, other_dataset = _ready_snapshot_and_dataset(tmp_path / "other", binding_index=1)
    blocked = dataclasses.replace(snapshot, status="blocked_gap")
    wrong_session_profile = dataclasses.replace(
        snapshot,
        volume_profile=volume_profile(snapshot.instrument_id, snapshot.observed_at.date() - dt.timedelta(days=1)),
    )

    for candidate, directory, threshold in (
        (snapshot, other_dataset, 10_000),
        (other_snapshot, dataset, 10_000),
        (blocked, dataset, 10_000),
        (wrong_session_profile, dataset, 10_000),
        (snapshot, dataset, 0),
        (snapshot, dataset, 100_001),
    ):
        with pytest.raises(UsSipTypedFeatureExtractionError, match="blocked") as captured:
            extract_us_sip_typed_feature_claims(
                candidate,
                directory,
                minimum_rvol_bps=threshold,
            )
        assert repr(captured.value) == "UsSipTypedFeatureExtractionError()"


def test_missing_latest_event_and_future_normalization_fail_closed(tmp_path: Path) -> None:
    snapshot, dataset = _ready_snapshot_and_dataset(tmp_path)

    missing_latest = dataclasses.replace(
        snapshot,
        source_end_at=cast(dt.datetime, snapshot.source_end_at).replace(minute=3),
    )
    before_normalization = dataclasses.replace(
        snapshot,
        observed_at=snapshot.observed_at.replace(minute=5, second=0),
    )

    for candidate in (missing_latest, before_normalization):
        with pytest.raises(UsSipTypedFeatureExtractionError, match="blocked"):
            extract_us_sip_typed_feature_claims(
                candidate,
                dataset,
                minimum_rvol_bps=10_000,
            )


def test_multi_owner_projection_keeps_instruments_in_separate_read_models(tmp_path: Path) -> None:
    result = _ready_result(tmp_path)

    models = project_us_sip_research_evidence(
        result.bindings,
        tmp_path / "runtime" / "canonical",
        minimum_rvol_bps=10_000,
    )

    assert len(models) == 2
    assert all(model.source_event_count == 35 for model in models)
    assert all(len(model.claims) == 2 for model in models)
    assert all(
        claim.corroboration_status is ClaimCorroborationStatus.UNCONFIRMED for model in models for claim in model.claims
    )
    entity_sets = {tuple(entity.canonical_id for entity in model.claims[0].entity_refs) for model in models}
    assert len(entity_sets) == 2

    with pytest.raises(UsSipResearchEvidenceProjectionError, match="blocked"):
        project_us_sip_research_evidence(
            result.bindings,
            tmp_path / "missing",
            minimum_rvol_bps=10_000,
        )


def _ready_snapshot_and_dataset(tmp_path: Path, *, binding_index: int = 0):
    result = _ready_result(tmp_path)
    snapshot = result.bindings[binding_index].snapshot
    matches = tuple((tmp_path / "runtime" / "canonical").rglob(f"dataset_id={snapshot.identity.dataset_id}"))
    assert len(matches) == 1
    return snapshot, matches[0]


def _ready_result(tmp_path: Path):
    def respond(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.params["symbols"]
        return httpx2.Response(
            200,
            json={"bars": {symbol: wire_bars(symbol, 35)}, "next_page_token": None},
        )

    return fleet(tmp_path / "runtime", respond).run_cycle(decision(), feature_requests())
