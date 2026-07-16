from __future__ import annotations

import pytest

from trading_agent.kr_instrument import (
    KR_INSTRUMENT_SCHEMA_V1,
    KR_INSTRUMENT_SCHEMA_V2,
    is_kr_instrument_symbol_v1,
    is_kr_instrument_symbol_v2,
)


def test_kr_instrument_symbol_versions_are_explicit() -> None:
    assert KR_INSTRUMENT_SCHEMA_V1 == 1
    assert KR_INSTRUMENT_SCHEMA_V2 == 2
    assert is_kr_instrument_symbol_v1("005930")
    assert not is_kr_instrument_symbol_v1("1234A0")
    assert is_kr_instrument_symbol_v2("005930")
    assert is_kr_instrument_symbol_v2("1234A0")


@pytest.mark.parametrize(
    "value",
    (
        "1234a0",
        " 005930",
        "005930 ",
        "00593",
        "0059300",
        "005-30",
        "005\n30",
        "가05930",
        "",
    ),
)
def test_kr_instrument_v2_rejects_noncanonical_symbols(value: str) -> None:
    assert not is_kr_instrument_symbol_v2(value)


def test_kr_instrument_validators_reject_non_strings() -> None:
    assert not is_kr_instrument_symbol_v1(5930)  # type: ignore[arg-type]
    assert not is_kr_instrument_symbol_v2(5930)  # type: ignore[arg-type]
