from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.kr_volume_surge_models import (
    InvalidKrVolumeSurgePayloadError,
    KrVolumeSurgePayload,
    KrVolumeSurgePayloadV2,
    KrVolumeSurgeSymbol,
    KrVolumeSurgeSymbolV2,
    canonical_kr_volume_surge_payload,
    parse_kr_volume_surge_payload,
)

SOURCE_AT = dt.datetime(2026, 7, 16, 1, 1, tzinfo=dt.UTC)
DERIVED_AT = SOURCE_AT + dt.timedelta(seconds=5)


def test_volume_surge_v1_replay_stays_numeric_only() -> None:
    payload = KrVolumeSurgePayload(
        observed_at=DERIVED_AT,
        symbols=(
            KrVolumeSurgeSymbol(
                symbol="005930",
                trading_value_krw=Decimal("100"),
                volume_ratio=Decimal("2.5"),
            ),
        ),
    )

    raw = canonical_kr_volume_surge_payload(payload)

    assert json.loads(raw)["schema_version"] == 1
    assert parse_kr_volume_surge_payload(raw) == payload
    with pytest.raises(ValidationError):
        _ = KrVolumeSurgeSymbol(
            symbol="1234A0",
            trading_value_krw=Decimal("100"),
            volume_ratio=Decimal("2.5"),
        )


def test_volume_surge_v1_replay_accepts_historical_missing_schema_version() -> None:
    raw = json.dumps(
        {
            "observed_at": DERIVED_AT.isoformat(),
            "symbols": [
                {
                    "symbol": "005930",
                    "trading_value_krw": "100",
                    "volume_ratio": "2.5",
                }
            ],
        }
    ).encode()

    parsed = parse_kr_volume_surge_payload(raw)

    assert isinstance(parsed, KrVolumeSurgePayload)
    assert parsed.schema_version == 1
    assert parsed.symbols[0].symbol == "005930"


def test_volume_surge_v2_preserves_alphanumeric_lineage() -> None:
    payload = _v2_payload()

    raw = canonical_kr_volume_surge_payload(payload)

    assert raw == canonical_kr_volume_surge_payload(payload)
    assert json.loads(raw)["schema_version"] == 2
    assert parse_kr_volume_surge_payload(raw) == payload
    assert payload.symbols[0].symbol == "1234A0"
    assert payload.symbols[0].source_catalyst_id == "a" * 64


def test_volume_surge_v2_accepts_numeric_and_empty_snapshots() -> None:
    numeric = _v2_payload(symbol="005930")
    empty = KrVolumeSurgePayloadV2(
        observed_at=DERIVED_AT,
        source_observed_at=SOURCE_AT,
        source_run_id="cycle-001:kis_ranking",
        symbols=(),
    )

    assert numeric.symbols[0].symbol == "005930"
    assert parse_kr_volume_surge_payload(canonical_kr_volume_surge_payload(empty)) == empty


def test_volume_surge_v2_requires_canonical_symbol_and_finite_metrics() -> None:
    with pytest.raises(ValidationError):
        _ = _v2_symbol(symbol="1234a0")
    with pytest.raises(ValidationError):
        _ = _v2_symbol(trading_value_krw=Decimal("-1"))
    with pytest.raises(ValidationError):
        _ = _v2_symbol(volume_ratio=Decimal("NaN"))
    with pytest.raises(ValidationError):
        _ = _v2_symbol(source_catalyst_id="not-a-sha")


def test_volume_surge_v2_requires_sorted_unique_symbols_and_lineage() -> None:
    first = _v2_symbol(symbol="005930", source_catalyst_id="a" * 64)
    second = _v2_symbol(symbol="1234A0", source_catalyst_id="b" * 64)
    duplicate_source = _v2_symbol(symbol="999999", source_catalyst_id="a" * 64)

    with pytest.raises(ValidationError):
        _ = _v2_payload(symbols=(second, first))
    with pytest.raises(ValidationError):
        _ = _v2_payload(symbols=(first, first))
    with pytest.raises(ValidationError):
        _ = _v2_payload(symbols=(first, duplicate_source))


def test_volume_surge_v2_requires_causal_aware_times_and_source_run() -> None:
    with pytest.raises(ValidationError):
        _ = _v2_payload(observed_at=SOURCE_AT - dt.timedelta(seconds=1))
    with pytest.raises(ValidationError):
        _ = _v2_payload(source_observed_at=SOURCE_AT.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _ = _v2_payload(source_run_id="cycle-001:volume_surge")
    with pytest.raises(ValidationError):
        _ = _v2_payload(source_run_id="../cycle:kis_ranking")


@pytest.mark.parametrize(
    "document",
    (
        {"schema_version": 3},
        {"schema_version": "2"},
        [],
        None,
    ),
)
def test_volume_surge_parser_rejects_unknown_schema_without_private_text(
    document: object,
) -> None:
    private_marker = "private-symbol-1234A0"
    raw = json.dumps(
        {"document": document, "private": private_marker}
        if not isinstance(document, dict)
        else document | {"private": private_marker}
    ).encode()

    with pytest.raises(InvalidKrVolumeSurgePayloadError) as captured:
        _ = parse_kr_volume_surge_payload(raw)

    assert private_marker not in str(captured.value)


def test_volume_surge_parser_rejects_malformed_json_and_extra_fields() -> None:
    with pytest.raises(InvalidKrVolumeSurgePayloadError):
        _ = parse_kr_volume_surge_payload(b"not-json-private-marker")
    raw = canonical_kr_volume_surge_payload(_v2_payload())
    document = json.loads(raw)
    document["unexpected"] = "private-marker"
    with pytest.raises(InvalidKrVolumeSurgePayloadError) as captured:
        _ = parse_kr_volume_surge_payload(json.dumps(document).encode())
    assert "private-marker" not in str(captured.value)


def _v2_symbol(
    *,
    symbol: str = "1234A0",
    trading_value_krw: Decimal = Decimal("100"),
    volume_ratio: Decimal = Decimal("2.5"),
    source_catalyst_id: str = "a" * 64,
) -> KrVolumeSurgeSymbolV2:
    return KrVolumeSurgeSymbolV2(
        symbol=symbol,
        trading_value_krw=trading_value_krw,
        volume_ratio=volume_ratio,
        source_catalyst_id=source_catalyst_id,
    )


def _v2_payload(
    *,
    symbol: str = "1234A0",
    observed_at: dt.datetime = DERIVED_AT,
    source_observed_at: dt.datetime = SOURCE_AT,
    source_run_id: str = "cycle-001:kis_ranking",
    symbols: tuple[KrVolumeSurgeSymbolV2, ...] | None = None,
) -> KrVolumeSurgePayloadV2:
    return KrVolumeSurgePayloadV2(
        observed_at=observed_at,
        source_observed_at=source_observed_at,
        source_run_id=source_run_id,
        symbols=(_v2_symbol(symbol=symbol),) if symbols is None else symbols,
    )
