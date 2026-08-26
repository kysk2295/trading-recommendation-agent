from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

import trading_agent.browser_social_evidence_sqlite as evidence_sqlite
from tests.browser_social_evidence_support import evidence
from trading_agent.browser_social_evidence_store import (
    BrowserSocialEvidenceStore,
    InvalidBrowserSocialEvidenceStoreError,
)


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "mode", "owner"))
def test_store_rejects_untrusted_database_identity(tmp_path: Path, kind: str) -> None:
    # Given: a database name with an unsafe inode, permission, or owner contract.
    path = tmp_path / "browser-social.sqlite3"
    owner_id = os.geteuid()
    if kind == "owner":
        owner_id += 1
    else:
        target = tmp_path / "target.sqlite3"
        target.touch(mode=0o600)
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        else:
            target.rename(path)
            path.chmod(0o640)
    # When/Then: no operation can open the authority.
    store = BrowserSocialEvidenceStore(path, owner_id=owner_id)
    with pytest.raises(InvalidBrowserSocialEvidenceStoreError):
        _ = store.append(evidence())


def test_store_rejects_parent_symlink(tmp_path: Path) -> None:
    # Given: a store path routed through a symlinked private-looking parent.
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    # When/Then: descriptor-pinned parent traversal fails closed.
    with pytest.raises(InvalidBrowserSocialEvidenceStoreError):
        _ = BrowserSocialEvidenceStore(linked / "browser-social.sqlite3").append(evidence())


def test_store_rejects_database_name_replacement_during_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an attacker replaces the database name after sqlite opens the original inode.
    path = tmp_path / "browser-social.sqlite3"
    real_connect = evidence_sqlite.sqlite3.connect

    def replace_after_connect(database: Path, timeout: float) -> sqlite3.Connection:
        connection = real_connect(database, timeout=timeout)
        path.unlink()
        path.touch(mode=0o600)
        return connection

    monkeypatch.setattr(evidence_sqlite.sqlite3, "connect", replace_after_connect)
    # When/Then: descriptor/name identity mismatch prevents schema or append work.
    with pytest.raises(InvalidBrowserSocialEvidenceStoreError):
        _ = BrowserSocialEvidenceStore(path).append(evidence())


def test_database_context_rechecks_name_when_operation_also_fails(tmp_path: Path) -> None:
    # Given: an open descriptor whose public database name is replaced.
    path = tmp_path / "browser-social.sqlite3"
    # When: replacement and an operation error occur in the same context.
    with (
        pytest.raises(evidence_sqlite.InvalidPrivateBrowserSocialEvidenceDatabaseError),
        evidence_sqlite.open_private_browser_social_evidence_database(path, os.geteuid()),
    ):
        path.unlink()
        path.touch(mode=0o600)
        raise sqlite3.DatabaseError
    # Then: the descriptor/name identity failure takes precedence over the operation error.


def test_store_rejects_version_spoof_without_exact_schema(tmp_path: Path) -> None:
    # Given: an attacker-created version-one database without immutable triggers.
    path = tmp_path / "browser-social.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE browser_social_evidence (evidence_id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version=1")
    path.chmod(0o600)
    # When/Then: user_version alone cannot satisfy exact schema verification.
    with pytest.raises(InvalidBrowserSocialEvidenceStoreError):
        _ = BrowserSocialEvidenceStore(path).get("a" * 64)


def test_two_writers_initialize_and_append_to_fresh_database(tmp_path: Path) -> None:
    # Given: two writers released simultaneously against one absent database.
    path = tmp_path / "browser-social.sqlite3"
    start = threading.Barrier(2)
    failures: list[str] = []

    def append(receipt_character: str) -> None:
        start.wait(timeout=2.0)
        try:
            _ = BrowserSocialEvidenceStore(path).append(evidence(receipt_character=receipt_character))
        except InvalidBrowserSocialEvidenceStoreError as error:
            failures.append(error.reason)

    workers = tuple(threading.Thread(target=append, args=(character,)) for character in ("b", "c"))
    # When: both writers initialize and append.
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3.0)
    # Then: initialization serializes and both bounded records survive.
    store = BrowserSocialEvidenceStore(path)
    assert failures == [] and all(not worker.is_alive() for worker in workers)
    assert len(store.search("semiconductor", limit=20)) == 2


def test_browser_store_does_not_modify_official_api_contract() -> None:
    # Given: the existing official-API-only entitlement contract.
    from trading_agent.social_evidence_models import SocialEntitlementContract

    # When: browser evidence modules are imported independently.
    fields = SocialEntitlementContract.model_fields
    # Then: official API and crawl restrictions remain mandatory on that separate contract.
    assert fields["official_api_only"].default is True
    assert fields["unauthorized_crawl_forbidden"].default is True
