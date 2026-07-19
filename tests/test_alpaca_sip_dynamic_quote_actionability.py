from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_feature_bridge as trade_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests import test_alpaca_sip_dynamic_trade_history as history_fixtures
from tests.test_us_quote_actionability import _conditional_publication
from trading_agent.alpaca_sip_dynamic_feature_bundle import build_alpaca_sip_dynamic_feature_bundle
from trading_agent.alpaca_sip_dynamic_quote_actionability import (
    alpaca_sip_quote_actionability_artifacts_match,
    assess_alpaca_sip_dynamic_quote,
)
from trading_agent.alpaca_sip_dynamic_quote_history import materialize_alpaca_sip_dynamic_quote_history_as_of
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_trade_history import materialize_alpaca_sip_dynamic_trade_history_as_of
from trading_agent.signal_contract_models import SignalActionability
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability import QuoteAssessmentStatus

_OFFSET_MS = 35 * 60 * 1_000
_OBSERVED = dynamic_fixtures._NOW + dt.timedelta(milliseconds=_OFFSET_MS + 11)
_SCAN_STARTED_AT = _OBSERVED - dt.timedelta(seconds=20)
_EPOCH = "1" * 32


def test_complete_bundle_creates_provider_authentic_waiting_signal(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, bid=100.01, ask=100.03)
    base = _base(entry="100.10", stop="99.00")

    first = assess_alpaca_sip_dynamic_quote(base, bundle, scan_started_at=_SCAN_STARTED_AT)
    second = assess_alpaca_sip_dynamic_quote(base, bundle, scan_started_at=_SCAN_STARTED_AT)

    assert second == first
    assert first.assessment.status is QuoteAssessmentStatus.VALIDATED_WAITING
    assert first.policy_evidence is not None
    assert first.policy_evidence.quote_id == f"us-quote:{bundle.bundle_id}"
    assert first.policy_evidence.evidence_ref.namespace == "quote/alpaca-sip-dynamic-bundle"
    assert first.policy_evidence.evidence_ref.record_id == bundle.bundle_id
    assert first.bundle.quote_confirmation.bid_exchange == "V"
    assert first.bundle.quote_confirmation.ask_exchange == "V"
    assert first.bundle.quote_confirmation.connection_epoch == _EPOCH
    assert first.derived_publication is not None
    assert first.derived_publication.signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
    assert "quote/snapshot" not in {item.namespace for item in first.derived_publication.signal.evidence_refs}
    assert alpaca_sip_quote_actionability_artifacts_match(base, first)


def test_bundle_quote_at_trigger_creates_trigger_reached_signal(tmp_path: Path) -> None:
    decision = assess_alpaca_sip_dynamic_quote(
        _base(entry="100.10", stop="99.00"),
        _bundle(tmp_path, bid=100.08, ask=100.10),
        scan_started_at=_SCAN_STARTED_AT,
    )

    assert decision.assessment.status is QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED
    assert decision.derived_publication is not None


@pytest.mark.parametrize(
    ("bid", "ask", "entry", "stop", "expected"),
    (
        (100.00, 101.00, "101.10", "99.00", QuoteAssessmentStatus.SPREAD_TOO_WIDE),
        (99.00, 99.01, "100.10", "99.00", QuoteAssessmentStatus.SETUP_INVALIDATED),
        (100.20, 100.31, "100.10", "99.00", QuoteAssessmentStatus.ENTRY_SLIPPAGE_EXCEEDED),
    ),
)
def test_bundle_quote_policy_blocks_terminal_risk_conditions(
    tmp_path: Path,
    bid: float,
    ask: float,
    entry: str,
    stop: str,
    expected: QuoteAssessmentStatus,
) -> None:
    decision = assess_alpaca_sip_dynamic_quote(
        _base(entry=entry, stop=stop),
        _bundle(tmp_path, bid=bid, ask=ask),
        scan_started_at=_SCAN_STARTED_AT,
    )

    assert decision.assessment.status is expected
    assert decision.policy_evidence is not None
    assert decision.derived_publication is None


def test_bundle_symbol_mismatch_is_provider_failure_without_policy_evidence(tmp_path: Path) -> None:
    base = _base(entry="100.10", stop="99.00")
    payload = base.model_dump(mode="json")
    payload["signal"]["symbol"] = "BBB"
    mismatched = TradeSignalPublication.model_validate(payload)

    decision = assess_alpaca_sip_dynamic_quote(
        mismatched,
        _bundle(tmp_path, bid=100.01, ask=100.03),
        scan_started_at=_SCAN_STARTED_AT,
    )

    assert decision.assessment.status is QuoteAssessmentStatus.PROVIDER_FAILED
    assert decision.policy_evidence is None
    assert decision.derived_publication is None
    assert alpaca_sip_quote_actionability_artifacts_match(mismatched, decision)


def _base(*, entry: str, stop: str) -> TradeSignalPublication:
    base = _conditional_publication(anchor=_OBSERVED, entry=entry, stop=stop)
    payload = base.model_dump(mode="json")
    payload["signal"]["symbol"] = "AAA"
    return TradeSignalPublication.model_validate(payload)


def _bundle(tmp_path: Path, *, bid: float, ask: float):
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")
    history_fixtures._append_epoch(
        store,
        _EPOCH,
        _OFFSET_MS,
        dynamic_fixtures._frame(
            quote_fixtures._quote(bid, ask, bid_size=300, ask_size=100),
            trade_fixtures._trade(101, (bid + ask) / 2),
        ),
    )
    plan = dynamic_fixtures._plan()
    snapshot = quote_fixtures._snapshot()
    trade_history = materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        plan,
        as_of=_OBSERVED,
    )
    quote_history = materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        plan,
        as_of=_OBSERVED,
    )
    return build_alpaca_sip_dynamic_feature_bundle(snapshot, trade_history, quote_history)
