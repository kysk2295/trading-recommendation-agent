from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from tests.browser_social_evidence_support import CAPTURED_AT, evidence
from trading_agent.browser_social_evidence_store import (
    BrowserSocialEvidenceConflictError,
    BrowserSocialEvidenceStore,
    InvalidBrowserSocialEvidenceStoreError,
)


def test_browser_observation_preserves_source_and_capture_lineage(tmp_path: Path) -> None:
    # Given: a fresh browser-only social evidence authority.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    captured = evidence()
    # When: one observation is appended and read after restart.
    assert store.append(captured) is True
    persisted = BrowserSocialEvidenceStore(store.path).get(captured.evidence_id)
    # Then: bounded source, receipt, chronology, and content lineage survive.
    assert persisted is not None
    assert persisted.browser_receipt_id == captured.browser_receipt_id
    assert persisted.first_observed_at <= persisted.captured_at
    assert persisted.content_sha256 == hashlib.sha256(persisted.excerpt.encode()).hexdigest()


def test_exact_replay_is_idempotent_and_changed_payload_conflicts(tmp_path: Path) -> None:
    # Given: two valid payloads with the same capture identity but different bounded content.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    first = evidence()
    changed = evidence(title="Changed title")
    assert first.evidence_id == changed.evidence_id and first != changed
    # When: first append, replay, and conflicting append are attempted.
    assert store.append(first) is True
    assert store.append(first) is False
    # Then: identity reuse cannot rewrite the original.
    with pytest.raises(BrowserSocialEvidenceConflictError):
        _ = store.append(changed)
    assert store.get(first.evidence_id) == first


def test_database_is_private_append_only_and_has_no_raw_columns(tmp_path: Path) -> None:
    # Given: a database containing one bounded evidence record.
    path = tmp_path / "browser-social.sqlite3"
    store = BrowserSocialEvidenceStore(path)
    assert store.append(evidence())
    # When: its filesystem and SQL contracts are inspected.
    metadata = os.lstat(path)
    with sqlite3.connect(path) as connection:
        columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(browser_social_evidence)"))
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE browser_social_evidence SET title='rewrite'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM browser_social_evidence")
    # Then: the file is private and mutation/raw browser-state columns do not exist.
    assert stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_nlink == 1
    assert triggers == {"browser_social_evidence_no_update", "browser_social_evidence_no_delete"}
    assert not {"headers", "cookies", "auth_token", "account_id", "full_html", "raw_page_body"} & set(columns)


def test_search_escapes_wildcards_and_orders_by_capture_then_id(tmp_path: Path) -> None:
    # Given: literal wildcard content, newer content, and equal-time records.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    wildcard = evidence(
        receipt_character="b",
        url="https://example.com/margin/story",
        title="Margin 100%_covered",
        excerpt="Literal percentage and underscore metrics.",
    )
    older = evidence(receipt_character="c", title="Semiconductor older")
    newest = evidence(
        receipt_character="d",
        title="Semiconductor newest",
        captured_at=CAPTURED_AT + timedelta(minutes=1),
    )
    tied = evidence(receipt_character="e", title="Semiconductor tied")
    for item in (wildcard, older, newest, tied):
        assert store.append(item)
    # When: literal wildcard and normal bounded searches run.
    literal = store.search("100%_", limit=20)
    ordered = store.search("semiconductor", limit=3)
    # Then: LIKE metacharacters are literal and ordering is deterministic.
    assert literal == (wildcard,)
    assert ordered == tuple(
        sorted((newest, older, tied), key=lambda item: (-item.captured_at.timestamp(), item.evidence_id))
    )


@pytest.mark.parametrize("limit", (0, 21, True))
def test_search_rejects_out_of_range_or_boolean_limit(tmp_path: Path, limit: int) -> None:
    # Given: an initialized bounded store.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    assert store.append(evidence())
    # When/Then: invalid result bounds fail at the public boundary.
    with pytest.raises(InvalidBrowserSocialEvidenceStoreError):
        _ = store.search("semiconductor", limit=limit)


def test_search_matches_all_bounded_projection_fields(tmp_path: Path) -> None:
    # Given: distinct terms across title, author, excerpt, and normalized URL.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    captured = evidence(
        title="Fab expansion",
        author_label="SignalAuthor",
        excerpt="Packaging capacity rises",
        url="https://example.com/wafer-route",
    )
    assert store.append(captured)
    # When/Then: every allowed projection field is searchable without page-body access.
    assert all(store.search(term, limit=1) == (captured,) for term in ("Fab", "SignalAuthor", "Packaging", "wafer"))


def test_search_treats_backslash_as_literal(tmp_path: Path) -> None:
    # Given: one title contains a literal backslash and another omits it.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    literal = evidence(receipt_character="b", title=r"Desk\Signal")
    other = evidence(receipt_character="c", title="Desk Signal")
    assert store.append(literal) and store.append(other)
    # When: the literal backslash is searched.
    results = store.search(r"Desk\Signal", limit=20)
    # Then: SQL LIKE escaping returns only the literal record.
    assert results == (literal,)


def test_store_accepts_maximum_bounded_unicode_record(tmp_path: Path) -> None:
    # Given: a model-valid record whose canonical escaped JSON exceeds eight KiB.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    captured = evidence(title="가" * 500, author_label="나" * 200, excerpt="다" * 2_000)
    # When: the maximum bounded record is appended.
    appended = store.append(captured)
    # Then: the storage boundary preserves the complete bounded projection.
    assert appended is True and store.get(captured.evidence_id) == captured


def test_get_rejects_malformed_identifier_before_sql(tmp_path: Path) -> None:
    # Given: an uninitialized store and malformed caller input.
    store = BrowserSocialEvidenceStore(tmp_path / "browser-social.sqlite3")
    # When/Then: the public boundary fails closed without creating authority state.
    with pytest.raises(InvalidBrowserSocialEvidenceStoreError):
        _ = store.get("not-a-digest")
    assert not store.path.exists()
