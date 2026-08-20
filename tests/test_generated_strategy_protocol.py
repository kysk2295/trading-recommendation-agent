from __future__ import annotations

import datetime as dt
import json

import pytest

from trading_agent.generated_strategy_protocol import (
    GeneratedStrategyProtocolError,
    NoSignalResponse,
    SignalResponse,
    encode_frame,
    observe_request,
    parse_runner_frame,
    signal_from_response,
)
from trading_agent.models import BarInput, MomentumCandidate


def test_protocol_round_trips_one_canonical_completed_bar() -> None:
    # Given: one completed bar and its point-in-time scanner candidate.
    bar = _bar()
    candidate = MomentumCandidate(
        bar.symbol,
        bar.timestamp,
        bar.close,
        0.05,
        0.06,
        2.5,
        700_000.0,
        bar.spread_bps,
        "filing",
        minutes_from_open=17,
        theme_catalyst_count=3,
        catalyst_age_minutes=8,
        execution_review_sessions=6,
        estimated_slippage_bps=4.5,
        fill_quality_bps=7.5,
    )

    # When: the host builds a single ordered observation frame.
    request = observe_request(1, bar, candidate)
    payload = encode_frame(request)

    # Then: the canonical frame contains no future collection and ends at one newline.
    assert payload.endswith(b"\n")
    assert b'"kind":"observe"' in payload
    assert b"bars" not in payload
    assert request.bar.symbol == "TEST"
    assert request.candidate is not None
    assert request.candidate.minutes_from_open == 17
    assert request.candidate.theme_catalyst_count == 3
    assert request.candidate.catalyst_age_minutes == 8
    assert request.candidate.execution_review_sessions == 6
    assert request.candidate.estimated_slippage_bps == 4.5
    assert request.candidate.fill_quality_bps == 7.5


def test_protocol_rejects_oversized_extra_and_non_finite_frames() -> None:
    # Given: hostile runner frames that exceed or violate the exact schema.
    oversized = b"{" + (b"x" * (64 * 1024)) + b"}\n"
    extra = b'{"kind":"no_signal","sequence":1,"extra":true}\n'
    nan = (
        b'{"kind":"signal","sequence":1,"symbol":"TEST",'
        b'"timestamp":"2026-07-23T13:31:00Z","entry":NaN,'
        b'"stop":10,"rationale":"x"}\n'
    )

    # When/Then: each frame is rejected before it can become a host signal.
    for payload in (oversized, extra, nan):
        with pytest.raises(GeneratedStrategyProtocolError):
            _ = parse_runner_frame(payload)


def test_protocol_rejects_sequence_symbol_and_timestamp_substitution() -> None:
    # Given: one request and runner responses that substitute its causal identity.
    request = observe_request(2, _bar(), None)
    responses = (
        NoSignalResponse(sequence=1),
        SignalResponse(
            sequence=2,
            symbol="OTHER",
            timestamp=request.bar.timestamp,
            entry=11.0,
            stop=10.0,
            rationale="x",
        ),
        SignalResponse(
            sequence=2,
            symbol=request.bar.symbol,
            timestamp=request.bar.timestamp + dt.timedelta(minutes=1),
            entry=11.0,
            stop=10.0,
            rationale="x",
        ),
    )

    # When/Then: no out-of-order or cross-bar response crosses into StrategySignal.
    for response in responses:
        with pytest.raises(GeneratedStrategyProtocolError):
            _ = signal_from_response(request, response, "generated-python:test")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("targets", [11.5]),
        ("position_size", 100),
        ("provider", "alpaca"),
        ("authority", True),
        ("order", {"type": "market"}),
    ),
)
def test_protocol_rejects_generated_host_owned_fields(
    field: str,
    value: str | int | bool | list[float] | dict[str, str],
) -> None:
    # Given: an otherwise valid signal frame with one host-owned field injected.
    payload = {
        "kind": "signal",
        "sequence": 1,
        "symbol": "TEST",
        "timestamp": "2026-07-23T13:31:00Z",
        "entry": 11.0,
        "stop": 10.0,
        "rationale": "x",
        field: value,
    }

    # When/Then: the host parser rejects the whole frame at the trust boundary.
    with pytest.raises(GeneratedStrategyProtocolError):
        _ = parse_runner_frame((json.dumps(payload, separators=(",", ":")) + "\n").encode())


@pytest.mark.parametrize(("entry", "stop"), ((10.0, 10.0), (9.5, 10.0)))
def test_protocol_rejects_wrong_long_entry_stop_direction(
    entry: float,
    stop: float,
) -> None:
    # Given: a generated long candidate whose stop is not below entry.
    payload = {
        "kind": "signal",
        "sequence": 1,
        "symbol": "TEST",
        "timestamp": "2026-07-23T13:31:00Z",
        "entry": entry,
        "stop": stop,
        "rationale": "x",
    }

    # When/Then: the host parser rejects it before signal projection.
    with pytest.raises(GeneratedStrategyProtocolError):
        _ = parse_runner_frame((json.dumps(payload, separators=(",", ":")) + "\n").encode())


def _bar() -> BarInput:
    return BarInput(
        symbol="TEST",
        timestamp=dt.datetime(2026, 7, 23, 13, 31, tzinfo=dt.UTC),
        open=10.0,
        high=11.0,
        low=9.5,
        close=10.5,
        volume=100_000,
        prior_close=9.8,
        average_daily_volume=1_000_000,
        spread_bps=20.0,
        catalyst="filing",
    )
