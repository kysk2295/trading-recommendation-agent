from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_feature_bridge as trade_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests import test_alpaca_sip_dynamic_trade_history as history_fixtures
from tests.test_alpaca_sip_dynamic_quote_actionability import (
    _OBSERVED,
    _SCAN_STARTED_AT,
    _base,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_quote_actionability_projection import (
    AlpacaSipQuoteActionabilityProjectionError,
    project_alpaca_sip_quote_actionability,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.us_quote_actionability import QuoteAssessmentStatus

_OFFSET_MS = 35 * 60 * 1_000
_EPOCH = "1" * 32


def test_projector_materializes_bundle_and_appends_exactly_once(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path / "source")
    output = AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3")
    base = _base(entry="100.10", stop="99.00")

    first = project_alpaca_sip_quote_actionability(
        base,
        quote_fixtures._snapshot(),
        receipts,
        dynamic_fixtures._plan(),
        output,
        scan_started_at=_SCAN_STARTED_AT,
    )
    second = project_alpaca_sip_quote_actionability(
        base,
        quote_fixtures._snapshot(),
        receipts,
        dynamic_fixtures._plan(),
        output,
        scan_started_at=_SCAN_STARTED_AT,
    )

    assert first.appended is True
    assert second.appended is False
    assert second.decision == first.decision
    assert first.decision.assessment.status is QuoteAssessmentStatus.VALIDATED_WAITING
    assert len(output.records()) == 1


def test_projector_rejects_multi_epoch_history_without_output(tmp_path: Path) -> None:
    receipts = AlpacaSipDynamicReceiptStore(tmp_path / "source.sqlite3")
    payload = _payload()
    history_fixtures._append_epoch(
        receipts,
        _EPOCH,
        _OFFSET_MS,
        payload,
        failed=True,
    )
    history_fixtures._append_epoch(
        receipts,
        "2" * 32,
        _OFFSET_MS + 20,
        payload,
    )
    output_path = tmp_path / "actionability.sqlite3"

    with pytest.raises(AlpacaSipQuoteActionabilityProjectionError):
        _ = project_alpaca_sip_quote_actionability(
            _base(entry="100.10", stop="99.00"),
            dataclasses.replace(
                quote_fixtures._snapshot(),
                observed_at=_OBSERVED + dt.timedelta(milliseconds=20),
            ),
            receipts,
            dynamic_fixtures._plan(),
            AlpacaSipQuoteActionabilityStore(output_path),
            scan_started_at=_SCAN_STARTED_AT,
        )

    assert not output_path.exists()


def test_projector_rejects_snapshot_plan_mismatch_without_output(tmp_path: Path) -> None:
    output_path = tmp_path / "actionability.sqlite3"
    snapshot = dataclasses.replace(quote_fixtures._snapshot(), instrument_id="wrong")

    with pytest.raises(AlpacaSipQuoteActionabilityProjectionError):
        _ = project_alpaca_sip_quote_actionability(
            _base(entry="100.10", stop="99.00"),
            snapshot,
            _receipts(tmp_path / "source"),
            dynamic_fixtures._plan(),
            AlpacaSipQuoteActionabilityStore(output_path),
            scan_started_at=_SCAN_STARTED_AT,
        )

    assert not output_path.exists()


def _receipts(path: Path) -> AlpacaSipDynamicReceiptStore:
    store = AlpacaSipDynamicReceiptStore(path / "dynamic.sqlite3")
    history_fixtures._append_epoch(store, _EPOCH, _OFFSET_MS, _payload())
    return store


def _payload() -> bytes:
    return dynamic_fixtures._frame(
        quote_fixtures._quote(100.01, 100.03, bid_size=300, ask_size=100),
        trade_fixtures._trade(101, 100.02),
    )
