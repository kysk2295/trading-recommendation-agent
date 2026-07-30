from __future__ import annotations

import datetime as dt
import hashlib

import pytest

from trading_agent.canonical_event_models import CanonicalEntityRef, CanonicalEntityType
from trading_agent.data_capability_models import (
    DataCorrectionPolicy,
    DataRetentionPolicy,
    DataSourceId,
    RedistributionPolicy,
)
from trading_agent.social_evidence_models import (
    SocialEntitlementContract,
    SocialEvidenceContractError,
    SocialEvidenceSnapshot,
    SocialOperatingMode,
    SocialPlatform,
    SocialPostObservation,
)

UTC = dt.UTC
NOW = dt.datetime(2026, 7, 21, 15, 0, tzinfo=UTC)


def test_social_entitlement_is_shadow_only_and_official_api_bound() -> None:
    contract = SocialEntitlementContract(
        source_id=DataSourceId(provider="x", feed="x"),
        platform=SocialPlatform.X,
        entitlement_id="x-official-api-v1",
        effective_from=NOW - dt.timedelta(days=1),
        redistribution=RedistributionPolicy.NONE,
        retention=DataRetentionPolicy(
            raw_retention_days=30,
            derived_retention_days=365,
            deletion_required=True,
            correction_policy=DataCorrectionPolicy.APPEND_TOMBSTONE,
        ),
        allows_raw_text_storage=True,
    )
    assert contract.operating_mode is SocialOperatingMode.SHADOW_RESEARCH_ONLY
    assert contract.official_api_only is True
    assert contract.unauthorized_crawl_forbidden is True


def test_social_entitlement_rejects_raw_text_with_redistribution() -> None:
    from pydantic import ValidationError

    with pytest.raises((SocialEvidenceContractError, ValidationError)):
        SocialEntitlementContract(
            source_id=DataSourceId(provider="reddit", feed="reddit"),
            platform=SocialPlatform.REDDIT,
            entitlement_id="reddit-official-api-v1",
            effective_from=NOW - dt.timedelta(days=1),
            redistribution=RedistributionPolicy.DERIVED_ONLY,
            retention=DataRetentionPolicy(
                raw_retention_days=14,
                derived_retention_days=180,
                deletion_required=True,
                correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
            ),
            allows_raw_text_storage=True,
        )


def test_social_evidence_snapshot_forbids_order_and_lifecycle_authority() -> None:
    fingerprint = hashlib.sha256(b"post").hexdigest()
    observation = SocialPostObservation(
        platform=SocialPlatform.X,
        provider_post_id="post.1",
        author_id="author.1",
        language="en",
        posted_at=NOW - dt.timedelta(minutes=5),
        received_at=NOW - dt.timedelta(minutes=4),
        deleted_or_withheld=False,
        spam_or_bot_score_bps=100,
        raw_text_stored=False,
        content_fingerprint=fingerprint,
    )
    snapshot = SocialEvidenceSnapshot(
        snapshot_id=hashlib.sha256(b"snap").hexdigest(),
        source_id=DataSourceId(provider="x", feed="x"),
        entitlement_id="x-official-api-v1",
        observed_at=NOW,
        entity_refs=(
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id="us:aapl",
            ),
        ),
        observations=(observation,),
        independent_author_count=1,
        novelty_score_bps=2_500,
        burst_score_bps=1_000,
    )
    assert snapshot.order_authority is False
    assert snapshot.lifecycle_authority is False
    assert snapshot.operating_mode is SocialOperatingMode.SHADOW_RESEARCH_ONLY
