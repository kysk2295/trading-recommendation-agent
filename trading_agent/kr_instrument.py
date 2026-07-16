from __future__ import annotations

import re
from typing import Final

KR_INSTRUMENT_SCHEMA_V1: Final = 1
KR_INSTRUMENT_SCHEMA_V2: Final = 2

_KR_SYMBOL_V1 = re.compile(r"^[0-9]{6}$")
_KR_SYMBOL_V2 = re.compile(r"^[0-9A-Z]{6}$")


def is_kr_instrument_symbol_v1(value: str) -> bool:
    return isinstance(value, str) and _KR_SYMBOL_V1.fullmatch(value) is not None


def is_kr_instrument_symbol_v2(value: str) -> bool:
    return isinstance(value, str) and _KR_SYMBOL_V2.fullmatch(value) is not None
