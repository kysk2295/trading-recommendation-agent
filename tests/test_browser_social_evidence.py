from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from tests.browser_social_evidence_support import CAPTURED_AT, evidence_capture
from trading_agent.browser_social_evidence import (
    BrowserSocialEvidence,
    BrowserSocialEvidenceCapture,
    browser_social_evidence,
    browser_source_identity_sha256,
    canonical_browser_social_evidence_json,
)


def test_capture_builds_deterministic_lineage_and_provisional_clusters() -> None:
    # Given: one bounded browser capture.
    capture = evidence_capture()
    # When: the capture is projected twice.
    first = browser_social_evidence(capture)
    second = browser_social_evidence(capture)
    # Then: all identifiers and content lineage are deterministic.
    assert first == second
    assert first.content_sha256 == hashlib.sha256(first.excerpt.encode()).hexdigest()
    assert first.repost_cluster_id == first.content_sha256
    assert first.independent_source_cluster_id == first.source_identity_sha256
    canonical = canonical_browser_social_evidence_json(first)
    expected = json.dumps(
        {
            "author_label": "Example Markets",
            "browser_receipt_id": "a" * 64,
            "captured_at": "2026-08-26T03:30:00Z",
            "content_sha256": "397cab1bc3f610adf36998364d4392f485e4bc18cb0916b3ce76ea7d7def9730",
            "evidence_id": "3b738364da650c6ba445eefea17b4fc99c6795a9b78520045b9de0c98b6cc693",
            "excerpt": "Semiconductor demand accelerated during the current session.",
            "first_observed_at": "2026-08-26T03:25:00Z",
            "independent_source_cluster_id": "6492806b16c3cd598e714681760f064d6e604bb894060e2f9c3c0897f6b7dc9d",
            "normalized_url": "https://example.com/semiconductor/story",
            "published_at": "2026-08-26T03:20:00Z",
            "repost_cluster_id": "397cab1bc3f610adf36998364d4392f485e4bc18cb0916b3ce76ea7d7def9730",
            "schema_version": 1,
            "screenshot_sha256": "4441146b0fe1d5c6845af126ba5ce6003ea77d6b4cb04d14114f86a925c5dbca",
            "source_identity_sha256": "6492806b16c3cd598e714681760f064d6e604bb894060e2f9c3c0897f6b7dc9d",
            "source_kind": "news",
            "title": "Semiconductor capacity expands",
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert canonical == expected
    assert (
        hashlib.sha256(canonical.encode()).hexdigest()
        == "b08db85e914494f0257b96eb7491139a3c14f4e0ffd9fcc823f14aa59df9f606"
    )


@pytest.mark.parametrize("source_kind", ("social", "community", "news", "search", "web"))
def test_capture_accepts_each_bounded_source_kind(source_kind: str) -> None:
    # Given: a capture for one supported public source kind.
    values = evidence_capture().model_dump(mode="python")
    values["source_kind"] = source_kind
    # When: the boundary parses and projects it.
    captured = BrowserSocialEvidenceCapture.model_validate(values)
    result = browser_social_evidence(captured)
    # Then: the closed source kind is retained.
    assert result.source_kind == source_kind


def test_capture_normalizes_public_https_url_and_utc_timestamps() -> None:
    # Given: a public URL with a mixed-case host and fragment plus a non-UTC offset.
    capture = evidence_capture(
        url="https://Example.COM/story?symbol=005930#account-view",
        captured_at=datetime.fromisoformat("2026-08-26T12:30:00+09:00"),
    )
    # When: the evidence is built.
    result = browser_social_evidence(capture)
    # Then: only canonical public HTTPS and UTC timestamps remain.
    assert result.normalized_url == "https://example.com/story?symbol=005930"
    assert result.captured_at == CAPTURED_AT


def test_capture_allows_missing_published_at() -> None:
    # Given: a browser page without a reliable publication time.
    capture = evidence_capture(published_at=None)
    # When: the evidence is built.
    result = browser_social_evidence(capture)
    # Then: observation and capture lineage remain usable without invented time.
    assert result.published_at is None
    assert result.first_observed_at <= result.captured_at


@pytest.mark.parametrize(
    "changes",
    (
        {"first_observed_at": CAPTURED_AT + timedelta(seconds=1)},
        {"published_at": CAPTURED_AT},
        {"captured_at": datetime(2026, 8, 26, 3, 30)},
    ),
)
def test_capture_rejects_invalid_or_naive_time_lineage(changes: dict[str, datetime]) -> None:
    # Given: chronology that cannot be a causal browser observation.
    values = evidence_capture().model_dump(mode="python")
    values.update(changes)
    # When/Then: the trust boundary rejects it.
    with pytest.raises(ValidationError):
        _ = BrowserSocialEvidenceCapture.model_validate(values)


@pytest.mark.parametrize(
    "raw_field",
    ("full_html", "headers", "cookies", "auth_token", "account_id", "raw_page_body"),
)
def test_capture_rejects_raw_or_authenticated_page_fields(raw_field: str) -> None:
    # Given: a caller tries to add forbidden browser state to the bounded record.
    values = evidence_capture().model_dump(mode="python")
    values[raw_field] = "forbidden"
    # When/Then: extra fields fail closed.
    with pytest.raises(ValidationError):
        _ = BrowserSocialEvidenceCapture.model_validate(values)


def test_evidence_rejects_forged_content_digest_and_clusters() -> None:
    # Given: a valid evidence payload with forged derived digests.
    valid = browser_social_evidence(evidence_capture())
    values = valid.model_dump(mode="python")
    values.update(content_sha256="0" * 64, repost_cluster_id="1" * 64)
    # When/Then: direct model parsing verifies constructor invariants again.
    with pytest.raises(ValidationError):
        _ = BrowserSocialEvidence.model_validate(values)


@pytest.mark.parametrize(
    "url",
    (
        "http://example.com/story",
        "https://user:secret@example.com/story",
        "https://localhost/story",
        "https://127.0.0.1/story",
    ),
)
def test_capture_rejects_nonpublic_or_credentialed_url(url: str) -> None:
    # Given: an unsafe source URL.
    values = evidence_capture().model_dump(mode="python")
    values["normalized_url"] = url
    # When/Then: the shared public-HTTPS policy rejects it.
    with pytest.raises(ValidationError):
        _ = BrowserSocialEvidenceCapture.model_validate(values)


def test_source_identity_helper_is_bounded_and_deterministic() -> None:
    # Given: a public source identity label.
    identity = "https://example.com|Example Markets"
    # When: it is hashed twice.
    first = browser_source_identity_sha256(identity)
    second = browser_source_identity_sha256(identity)
    # Then: only its deterministic digest is retained.
    assert first == second == hashlib.sha256(identity.encode()).hexdigest()
    with pytest.raises(ValueError):
        _ = browser_source_identity_sha256("")
