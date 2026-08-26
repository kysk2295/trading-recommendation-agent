from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack

from trading_agent.browser_social_evidence import (
    BrowserSocialEvidence,
    BrowserSocialEvidenceCapture,
    BrowserSourceKind,
    browser_social_evidence,
    browser_source_identity_sha256,
)

CAPTURED_AT = datetime(2026, 8, 26, 3, 30, tzinfo=UTC)


class EvidenceChanges(TypedDict, total=False):
    receipt_character: str
    url: str
    source_kind: BrowserSourceKind
    title: str
    author_label: str
    excerpt: str
    captured_at: datetime
    published_at: datetime | None


def evidence_capture(**changes: Unpack[EvidenceChanges]) -> BrowserSocialEvidenceCapture:
    receipt_character = changes.get("receipt_character", "a")
    url = changes.get("url", "https://example.com/semiconductor/story")
    author_label = changes.get("author_label", "Example Markets")
    return BrowserSocialEvidenceCapture(
        browser_receipt_id=receipt_character * 64,
        normalized_url=url,
        source_kind=changes.get("source_kind", "news"),
        source_identity_sha256=browser_source_identity_sha256(f"{url}|{author_label}"),
        title=changes.get("title", "Semiconductor capacity expands"),
        author_label=author_label,
        excerpt=changes.get("excerpt", "Semiconductor demand accelerated during the current session."),
        published_at=changes.get("published_at", CAPTURED_AT - timedelta(minutes=10)),
        first_observed_at=CAPTURED_AT - timedelta(minutes=5),
        captured_at=changes.get("captured_at", CAPTURED_AT),
        screenshot_sha256=hashlib.sha256(b"screenshot").hexdigest(),
    )


def evidence(**changes: Unpack[EvidenceChanges]) -> BrowserSocialEvidence:
    return browser_social_evidence(evidence_capture(**changes))
