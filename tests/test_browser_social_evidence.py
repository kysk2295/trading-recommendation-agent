from __future__ import annotations

import hashlib
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
    assert hashlib.sha256(canonical_browser_social_evidence_json(first).encode()).hexdigest()


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
