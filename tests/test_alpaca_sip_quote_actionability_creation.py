from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.test_alpaca_sip_dynamic_quote_actionability import _SCAN_STARTED_AT, _base, _bundle
from trading_agent.alpaca_sip_dynamic_quote_actionability import assess_alpaca_sip_dynamic_quote
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    build_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import (
    AlpacaSipQuoteActionabilityStore,
    AlpacaSipQuoteActionabilityStoreError,
)
from trading_agent.trade_signal_publication import TradeSignalPublication


def test_manifest_append_atomically_records_artifact_creation(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    manifest = build_alpaca_sip_quote_actionability_manifest(
        base,
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")

    first = store.append_for_manifest(manifest, decision)
    replay = store.append_for_manifest(manifest, decision)

    assert first.appended is True
    assert replay.appended is False
    assert replay.creation == first.creation
    assert first.creation.artifact_id == decision.assessment.assessment_id
    assert first.creation.manifest_id == manifest.manifest_id
    assert first.creation.evaluated_at == manifest.snapshot.observed_at
    assert store.creations() == (first.creation,)
    assert len(store.records()) == 1


def test_v1_reader_is_not_migrated_until_manifest_writer(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    assert store.append(base, decision)

    assert store.creations() == ()
    assert store.records()[0].artifact_id == decision.assessment.assessment_id
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)


def test_manifest_writer_never_backfills_legacy_artifact_creation(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    manifest = build_alpaca_sip_quote_actionability_manifest(
        base,
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    assert store.append(base, decision)

    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.append_for_manifest(manifest, decision)

    assert store.creations() == ()
    assert len(store.records()) == 1


def test_v1_writer_migrates_only_when_appending_new_manifest_artifact(tmp_path: Path) -> None:
    first_base = _base(entry="100.10", stop="99.00")
    first_decision = assess_alpaca_sip_dynamic_quote(
        first_base,
        _bundle(tmp_path / "first", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    assert store.append(first_base, first_decision)
    payload = first_base.model_dump(mode="python")
    payload["signal"]["signal_id"] = "base-signal-2"
    second_base = TradeSignalPublication.model_validate(payload)
    second_manifest = build_alpaca_sip_quote_actionability_manifest(
        second_base,
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    second_decision = assess_alpaca_sip_dynamic_quote(
        second_base,
        _bundle(tmp_path / "second", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )

    appended = store.append_for_manifest(second_manifest, second_decision)

    assert appended.appended is True
    assert len(store.records()) == 2
    assert store.creations() == (appended.creation,)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def test_legacy_writer_cannot_append_unbound_artifact_after_v2_migration(tmp_path: Path) -> None:
    first_base = _base(entry="100.10", stop="99.00")
    first_manifest = build_alpaca_sip_quote_actionability_manifest(
        first_base,
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    first_decision = assess_alpaca_sip_dynamic_quote(
        first_base,
        _bundle(tmp_path / "first", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    _ = store.append_for_manifest(first_manifest, first_decision)
    payload = first_base.model_dump(mode="python")
    payload["signal"]["signal_id"] = "base-signal-2"
    second_base = TradeSignalPublication.model_validate(payload)
    second_decision = assess_alpaca_sip_dynamic_quote(
        second_base,
        _bundle(tmp_path / "second", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )

    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.append(second_base, second_decision)

    assert len(store.records()) == 1
    assert len(store.creations()) == 1


def test_creation_trigger_tamper_blocks_query_replay(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    manifest = build_alpaca_sip_quote_actionability_manifest(
        base,
        quote_fixtures._snapshot(),
        dynamic_fixtures._plan(),
        scan_started_at=_SCAN_STARTED_AT,
    )
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    _ = store.append_for_manifest(manifest, decision)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER alpaca_sip_quote_actionability_creation_no_update")

    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.creations()
