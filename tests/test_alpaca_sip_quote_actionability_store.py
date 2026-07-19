from __future__ import annotations

import dataclasses
import os
import sqlite3
from pathlib import Path

import pytest

from tests.test_alpaca_sip_dynamic_quote_actionability import (
    _SCAN_STARTED_AT,
    _base,
    _bundle,
)
from trading_agent.alpaca_sip_dynamic_quote_actionability import assess_alpaca_sip_dynamic_quote
from trading_agent.alpaca_sip_quote_actionability_store import (
    AlpacaSipQuoteActionabilityStore,
    AlpacaSipQuoteActionabilityStoreError,
)
from trading_agent.us_quote_actionability import QuoteAssessmentStatus


def test_store_appends_and_replays_complete_actionability_envelope(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")

    assert store.append(base, decision) is True
    assert store.append(base, decision) is False

    artifacts = store.records()
    assert len(artifacts) == 1
    assert artifacts[0].artifact_id == decision.assessment.assessment_id
    assert artifacts[0].base_publication == base
    assert artifacts[0].bundle == decision.bundle
    assert artifacts[0].policy_evidence == decision.policy_evidence
    assert artifacts[0].assessment == decision.assessment
    assert artifacts[0].derived_publication == decision.derived_publication
    assert artifacts[0].derived_publication is not None
    assert artifacts[0].assessment.status is QuoteAssessmentStatus.VALIDATED_WAITING
    assert (tmp_path / "actionability.sqlite3").stat().st_mode & 0o777 == 0o600


def test_store_rejects_second_terminal_for_same_base_scan_cycle(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    first = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "first", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    second = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "second", bid=100.08, ask=100.10),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")

    assert first.assessment.assessment_id == second.assessment.assessment_id
    assert store.append(base, first) is True
    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.append(base, second)
    assert store.records()[0].assessment.status is QuoteAssessmentStatus.VALIDATED_WAITING


def test_store_rejects_forged_decision_before_writing(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    forged = dataclasses.replace(
        decision,
        assessment=decision.assessment.model_copy(update={"status": QuoteAssessmentStatus.PROVIDER_FAILED}),
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")

    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.append(base, forged)
    assert not (tmp_path / "actionability.sqlite3").exists()


def test_store_is_append_only_and_private(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    path = tmp_path / "actionability.sqlite3"
    store = AlpacaSipQuoteActionabilityStore(path)
    assert store.append(base, decision) is True

    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute("UPDATE alpaca_sip_quote_actionability SET payload_sha256='x'")

    os.chmod(path, 0o644)
    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.records()


def test_store_rejects_hard_linked_database(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    os.link(store.path, tmp_path / "linked.sqlite3")

    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.records()


def test_store_rejects_missing_append_only_trigger(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        _ = connection.execute("DROP TRIGGER alpaca_sip_quote_actionability_no_update")

    with pytest.raises(AlpacaSipQuoteActionabilityStoreError):
        _ = store.records()


def _populated_store(tmp_path: Path) -> AlpacaSipQuoteActionabilityStore:
    base = _base(entry="100.10", stop="99.00")
    decision = assess_alpaca_sip_dynamic_quote(
        base,
        _bundle(tmp_path / "source", bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )
    store = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    assert store.append(base, decision) is True
    return store
