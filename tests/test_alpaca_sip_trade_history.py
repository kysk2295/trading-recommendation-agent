from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.alpaca_sip_trade_history import (
    AlpacaSipTradeHistoryError,
    AlpacaSipTradeHistoryRequest,
    AlpacaSipTradeInstrumentBinding,
    project_alpaca_sip_trade_history,
)
from trading_agent.alpaca_sip_trade_history_coverage import assess_alpaca_sip_trade_history_coverage
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipReceivedTradeFrame,
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
    parse_alpaca_sip_trade_frame,
)
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore, StoredAlpacaSipTradeFrame
from trading_agent.canonical_event_history import active_canonical_events_as_of, replay_canonical_event_history
from trading_agent.canonical_event_models import CanonicalEventOperation
from trading_agent.canonical_history_coverage import (
    IncompleteCanonicalHistoryError,
    require_complete_canonical_history,
)
from trading_agent.canonical_parquet_writer import write_canonical_dataset_parquet

_DATE = dt.date(2026, 7, 17)
_RECEIVED = dt.datetime(2026, 7, 17, 14, 30, 0, tzinfo=dt.UTC)


def test_parse_trade_frame_when_provider_sends_original_correction_and_cancel() -> None:
    # Given
    payload = _frame_payload(_original(), _correction(), _cancel(102))

    # When
    messages = parse_alpaca_sip_trade_frame(payload)

    # Then
    assert tuple(type(message) for message in messages) == (
        AlpacaSipTradeMessage,
        AlpacaSipTradeCorrectionMessage,
        AlpacaSipTradeCancelMessage,
    )


def test_project_history_when_correction_and_cancel_target_provider_aliases(tmp_path: Path) -> None:
    # Given
    frame = _stored_frame(tmp_path, _frame_payload(_original(), _correction(), _cancel(102)))
    request = _request()

    # When
    batch = project_alpaca_sip_trade_history((frame,), request)

    # Then
    ordered = tuple(sorted(batch.events, key=lambda event: event.normalized_at))
    assert tuple(event.operation for event in ordered) == (
        CanonicalEventOperation.ORIGINAL,
        CanonicalEventOperation.CORRECTION,
        CanonicalEventOperation.TOMBSTONE,
    )
    assert ordered[1].correction_of == ordered[0].event_id
    assert ordered[2].correction_of == ordered[1].event_id
    assert {event.provider_event_id for event in ordered} == {"2026-07-17:AAPL:101"}


def test_project_history_when_chain_is_tombstoned_has_no_active_trade(tmp_path: Path) -> None:
    # Given
    batch = project_alpaca_sip_trade_history(
        (_stored_frame(tmp_path, _frame_payload(_original(), _correction(), _cancel(102))),),
        _request(),
    )

    # When
    active = active_canonical_events_as_of(
        batch.events,
        as_of=_RECEIVED + dt.timedelta(seconds=1),
    )

    # Then
    assert active == ()


def test_project_history_when_original_is_missing_fails_closed(tmp_path: Path) -> None:
    # Given
    frame = _stored_frame(tmp_path, _frame_payload(_correction()))

    # When / Then
    with pytest.raises(AlpacaSipTradeHistoryError, match="could not be projected"):
        project_alpaca_sip_trade_history((frame,), _request())


def test_project_history_when_symbol_is_unbound_fails_closed(tmp_path: Path) -> None:
    # Given
    frame = _stored_frame(tmp_path, _frame_payload(_original(symbol="MSFT")))

    # When / Then
    with pytest.raises(AlpacaSipTradeHistoryError, match="could not be projected"):
        project_alpaca_sip_trade_history((frame,), _request())


def test_project_history_when_correction_original_values_disagree_fails_closed(tmp_path: Path) -> None:
    # Given
    correction = _correction()
    correction["op"] = 210.0
    frame = _stored_frame(tmp_path, _frame_payload(_original(), correction))

    # When / Then
    with pytest.raises(AlpacaSipTradeHistoryError, match="could not be projected"):
        project_alpaca_sip_trade_history((frame,), _request())


def test_project_history_when_correction_follows_tombstone_fails_closed(tmp_path: Path) -> None:
    # Given
    frame = _stored_frame(tmp_path, _frame_payload(_original(), _cancel(101), _correction()))

    # When / Then
    with pytest.raises(AlpacaSipTradeHistoryError, match="could not be projected"):
        project_alpaca_sip_trade_history((frame,), _request())


def test_project_history_when_chain_spans_raw_frames_replays_canonical_dataset(tmp_path: Path) -> None:
    # Given
    store = AlpacaSipTradeHistoryStore(tmp_path / "trade-history.sqlite3")
    original = store.append_frame(_frame(_frame_payload(_original())))
    correction = store.append_frame(
        AlpacaSipReceivedTradeFrame(
            _DATE,
            _RECEIVED + dt.timedelta(milliseconds=1),
            _frame_payload(_correction(), _cancel(102)),
        )
    )
    batch = project_alpaca_sip_trade_history((original, correction), _request())

    # When
    publication = write_canonical_dataset_parquet(batch, output_root=tmp_path / "canonical")
    replay = replay_canonical_event_history(
        (publication.dataset_directory,),
        as_of=_RECEIVED + dt.timedelta(seconds=1),
    )

    # Then
    assert replay.observed_event_count == 3
    assert replay.active_events == ()
    assert replay.tombstoned_root_event_ids == (min(batch.events, key=lambda event: event.normalized_at).event_id,)


def test_history_coverage_when_fixture_chain_is_valid_remains_incomplete(tmp_path: Path) -> None:
    # Given
    stored = _stored_frame(tmp_path, _frame_payload(_original(), _correction(), _cancel(102)))
    batch = project_alpaca_sip_trade_history((stored,), _request())

    # When
    coverage = assess_alpaca_sip_trade_history_coverage(batch)

    # Then
    assert coverage.raw_first_verified is True
    assert coverage.correction_supported is True
    assert coverage.tombstone_supported is True
    assert coverage.correction_observed is True
    assert coverage.tombstone_observed is True
    assert coverage.complete_history is False
    assert coverage.reason_codes == ("continuity_unattested",)


def test_complete_history_gate_when_continuity_is_unattested_blocks_evidence(tmp_path: Path) -> None:
    # Given
    stored = _stored_frame(tmp_path, _frame_payload(_original()))
    batch = project_alpaca_sip_trade_history((stored,), _request())
    coverage = assess_alpaca_sip_trade_history_coverage(batch)

    # When / Then
    with pytest.raises(IncompleteCanonicalHistoryError, match="incomplete"):
        require_complete_canonical_history(coverage)


def test_project_history_when_trade_id_repeats_next_market_date_keeps_distinct_identity(tmp_path: Path) -> None:
    # Given
    next_date = dt.date(2026, 7, 18)
    next_received = dt.datetime(2026, 7, 18, 14, 30, tzinfo=dt.UTC)
    first = _stored_frame(tmp_path / "first", _frame_payload(_original()))
    second = AlpacaSipTradeHistoryStore(tmp_path / "second" / "history.sqlite3").append_frame(
        AlpacaSipReceivedTradeFrame(
            next_date,
            next_received,
            _frame_payload(_original(timestamp="2026-07-18T14:29:59.123456Z")),
        )
    )

    # When
    first_event = project_alpaca_sip_trade_history((first,), _request()).events[0]
    second_event = project_alpaca_sip_trade_history((second,), _request_for(next_date)).events[0]

    # Then
    assert first_event.event_id != second_event.event_id
    assert first_event.provider_event_id == "2026-07-17:AAPL:101"
    assert second_event.provider_event_id == "2026-07-18:AAPL:101"


def test_project_history_when_wire_timestamp_has_different_market_date_fails_closed(tmp_path: Path) -> None:
    # Given
    received = dt.datetime(2026, 7, 18, 14, 30, tzinfo=dt.UTC)
    stored = AlpacaSipTradeHistoryStore(tmp_path / "history.sqlite3").append_frame(
        AlpacaSipReceivedTradeFrame(
            _DATE,
            received,
            _frame_payload(_original(timestamp="2026-07-18T14:29:59.123456Z")),
        )
    )

    # When / Then
    with pytest.raises(AlpacaSipTradeHistoryError, match="could not be projected"):
        project_alpaca_sip_trade_history((stored,), _request())


def test_store_when_malformed_frame_arrives_preserves_raw_before_parse(tmp_path: Path) -> None:
    # Given
    store = AlpacaSipTradeHistoryStore(tmp_path / "trade-history.sqlite3")
    malformed = _frame(b'[{"T":"q","S":"AAPL"}]')

    # When
    stored = store.append_frame(malformed)

    # Then
    assert store.frame_count() == 1
    assert stored.payload == malformed.payload
    with pytest.raises(AlpacaSipTradeHistoryError, match="could not be parsed"):
        parse_alpaca_sip_trade_frame(stored.payload)


def test_store_when_exact_frame_is_retried_is_idempotent(tmp_path: Path) -> None:
    # Given
    store = AlpacaSipTradeHistoryStore(tmp_path / "trade-history.sqlite3")
    frame = _frame(_frame_payload(_original()))

    # When
    first = store.append_frame(frame)
    second = store.append_frame(frame)

    # Then
    assert first == second
    assert store.frame_count() == 1
    assert store.load_frames(_DATE) == (first,)


def _request() -> AlpacaSipTradeHistoryRequest:
    return _request_for(_DATE)


def _request_for(market_date: dt.date) -> AlpacaSipTradeHistoryRequest:
    return AlpacaSipTradeHistoryRequest(
        market_date=market_date,
        bindings=(AlpacaSipTradeInstrumentBinding("AAPL", "us-equity-aapl"),),
    )


def _frame(payload: bytes) -> AlpacaSipReceivedTradeFrame:
    return AlpacaSipReceivedTradeFrame(_DATE, _RECEIVED, payload)


def _stored_frame(tmp_path: Path, payload: bytes) -> StoredAlpacaSipTradeFrame:
    return AlpacaSipTradeHistoryStore(tmp_path / "trade-history.sqlite3").append_frame(_frame(payload))


def _frame_payload(*messages: dict[str, str | int | float | list[str]]) -> bytes:
    return json.dumps(messages, separators=(",", ":")).encode()


def _original(
    symbol: str = "AAPL",
    timestamp: str = "2026-07-17T14:29:59.123456Z",
) -> dict[str, str | int | float | list[str]]:
    return {
        "T": "t",
        "S": symbol,
        "i": 101,
        "x": "V",
        "p": 211.25,
        "s": 40,
        "c": ["@"],
        "t": timestamp,
        "z": "C",
    }


def _correction() -> dict[str, str | int | float | list[str]]:
    return {
        "T": "c",
        "S": "AAPL",
        "x": "V",
        "oi": 101,
        "op": 211.25,
        "os": 40,
        "oc": ["@"],
        "ci": 102,
        "cp": 211.2,
        "cs": 35,
        "cc": ["@"],
        "t": "2026-07-17T14:29:59.123456Z",
        "z": "C",
    }


def _cancel(trade_id: int) -> dict[str, str | int | float | list[str]]:
    return {
        "T": "x",
        "S": "AAPL",
        "i": trade_id,
        "x": "V",
        "p": 211.2,
        "s": 35,
        "a": "C",
        "t": "2026-07-17T14:29:59.123456Z",
        "z": "C",
    }
