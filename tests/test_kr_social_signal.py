from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.browser_social_evidence_support import CAPTURED_AT, evidence_capture
from trading_agent.browser_social_evidence import (
    BrowserSocialEvidence,
    BrowserSocialEvidenceCapture,
    browser_social_evidence,
    canonical_browser_social_evidence_json,
)
from trading_agent.kr_social_signal_models import (
    KrSocialSignal,
    KrSocialSignalRequest,
    KrSocialVerificationState,
    normalize_kr_social_signal,
)


def _post(
    receipt_character: str,
    *,
    url: str = "https://example.com/semiconductor/story",
    excerpt: str = "Semiconductor demand accelerated during the current session.",
    published_offset: int = -10,
    observed_offset: int = -5,
) -> BrowserSocialEvidence:
    values = evidence_capture(receipt_character=receipt_character, url=url, excerpt=excerpt).model_dump(mode="python")
    values["published_at"] = CAPTURED_AT + timedelta(minutes=published_offset)
    values["first_observed_at"] = CAPTURED_AT + timedelta(minutes=observed_offset)
    values["captured_at"] = CAPTURED_AT + timedelta(minutes=observed_offset + 1)
    return browser_social_evidence(BrowserSocialEvidenceCapture.model_validate(values))


def _selected_posts() -> tuple[BrowserSocialEvidence, ...]:
    return (
        _post("a", published_offset=-15, observed_offset=-5),
        _post("b", published_offset=-10, observed_offset=-4),
        _post(
            "c",
            url="https://independent.example.com/semiconductor/story",
            excerpt="Independent source confirms semiconductor order momentum.",
            published_offset=-30,
            observed_offset=-20,
        ),
    )


def _request(posts: tuple[BrowserSocialEvidence, ...]) -> KrSocialSignalRequest:
    return KrSocialSignalRequest(
        task_id="a" * 64,
        symbol="005930",
        theme="Semiconductor demand",
        claim_summary="Independent reporting supports a semiconductor demand acceleration claim.",
        evidence_ids=tuple(sorted(post.evidence_id for post in posts)),
        normalized_at=CAPTURED_AT + timedelta(minutes=1),
    )


def test_normalizer_derives_immutable_multi_source_signal() -> None:
    # Given: two copied posts and one independent bounded browser source.
    posts = _selected_posts()
    request = _request(posts)
    # When: the exact requested evidence set is normalized.
    signal = normalize_kr_social_signal(request, posts)
    # Then: clusters, source hashes, chronology, and content identity are derived.
    assert signal.post_count == 3
    assert signal.repost_cluster_count == 2
    assert signal.independent_source_count == 2
    assert signal.verification_state is KrSocialVerificationState.MULTI_SOURCE_CORROBORATED
    assert signal.earliest_published_at == CAPTURED_AT - timedelta(minutes=30)
    assert signal.first_observed_at == CAPTURED_AT - timedelta(minutes=20)
    assert signal.source_payload_sha256s == tuple(
        sorted(
            hashlib.sha256(canonical_browser_social_evidence_json(post).encode("ascii")).hexdigest() for post in posts
        )
    )
    assert signal.repost_cluster_ids == tuple(sorted({post.repost_cluster_id for post in posts}))
    assert signal.independent_source_cluster_ids == tuple(
        sorted({post.independent_source_cluster_id for post in posts})
    )
    assert signal.evidence_ids == request.evidence_ids


def test_normalizer_is_deterministic_for_shuffled_selected_evidence() -> None:
    # Given: one selected set presented in its natural and reversed observation order.
    posts = _selected_posts()
    request = _request(posts)
    # When: each bounded sequence is normalized.
    natural = normalize_kr_social_signal(request, posts)
    shuffled = normalize_kr_social_signal(request, tuple(reversed(posts)))
    # Then: deterministic processing produces the identical immutable signal.
    assert shuffled == natural


@pytest.mark.parametrize("symbol", ("00593", "0059300", "abc123"))
def test_normalizer_rejects_invalid_kr_symbol(symbol: str) -> None:
    # Given: a request outside the KR instrument schema.
    posts = _selected_posts()
    request = KrSocialSignalRequest.model_validate(_request(posts).model_dump(mode="python") | {"symbol": symbol})
    # When/Then: signal construction fails closed.
    with pytest.raises(ValueError):
        _ = normalize_kr_social_signal(request, posts)


def test_normalizer_rejects_unknown_requested_evidence() -> None:
    # Given: a selected set whose request includes an unavailable identifier.
    posts = _selected_posts()
    request = KrSocialSignalRequest.model_validate(
        _request(posts).model_dump(mode="python")
        | {"evidence_ids": tuple(sorted((*_request(posts).evidence_ids, "d" * 64)))}
    )
    # When/Then: normalization cannot silently substitute evidence.
    with pytest.raises(ValueError):
        _ = normalize_kr_social_signal(request, posts)


@pytest.mark.parametrize("evidence_ids", (("b" * 64, "a" * 64), ("a" * 64, "a" * 64)))
def test_request_rejects_unsorted_or_duplicate_evidence_ids(evidence_ids: tuple[str, str]) -> None:
    # Given: an identity list that is not a sorted unique selection.
    values = _request(_selected_posts()).model_dump(mode="python") | {"evidence_ids": evidence_ids}
    # When/Then: the request boundary rejects ambiguous selection identity.
    with pytest.raises(ValueError):
        _ = KrSocialSignalRequest.model_validate(values)


@pytest.mark.parametrize("field", ("theme", "claim_summary"))
def test_request_rejects_blank_theme_or_claim(field: str) -> None:
    # Given: visible fields containing only whitespace.
    values = _request(_selected_posts()).model_dump(mode="python") | {field: "   "}
    # When/Then: a contentless request is invalid.
    with pytest.raises(ValueError):
        _ = KrSocialSignalRequest.model_validate(values)


def test_normalizer_rejects_publication_after_normalization() -> None:
    # Given: otherwise valid evidence published after the request cutoff.
    future = _post("d", published_offset=3, observed_offset=4)
    request = _request((future,))
    # When/Then: future publication cannot form a current signal.
    with pytest.raises(ValueError):
        _ = normalize_kr_social_signal(request, (future,))


def test_signal_model_rejects_forged_content_address() -> None:
    # Given: a normalized signal whose advertised identity is replaced.
    signal = normalize_kr_social_signal(_request(_selected_posts()), _selected_posts())
    # When/Then: direct parsing recomputes and verifies the content address.
    with pytest.raises(ValidationError):
        _ = KrSocialSignal.model_validate(signal.model_dump(mode="python") | {"signal_id": "0" * 64})
