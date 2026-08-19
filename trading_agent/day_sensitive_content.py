from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterable
from typing import Final
from urllib.parse import unquote

_SEPARATORS: Final = re.compile(r"[^a-z0-9]+")
_BASE64_CANDIDATE: Final = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{8,}={0,2}(?![A-Za-z0-9+/=])")
_SENSITIVE: Final = re.compile(
    r"\b(?:api key|auth header|authorization|bearer|credential|credentials|password|secret|token|"
    r"account (?:id|identifier|number|no)|provider (?:auth|authorization|credential|credentials|id|key|secret|token))\b"
)
_MAX_TEXT_BYTES: Final = 48 * 1024
_MAX_DECODED_BYTES: Final = 4 * 1024


def contains_sensitive_text(values: Iterable[str]) -> bool:
    return any(_contains_sensitive_value(value, depth=0) for value in values)


def _contains_sensitive_value(value: str, *, depth: int) -> bool:
    if len(value.encode(errors="ignore")) > _MAX_TEXT_BYTES:
        return True
    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    normalized = _SEPARATORS.sub(" ", unicodedata.normalize("NFKC", decoded).casefold()).strip()
    if _SENSITIVE.search(normalized) is not None:
        return True
    if depth >= 2:
        return False
    for match in _BASE64_CANDIDATE.finditer(decoded):
        candidate = match.group()
        if len(candidate) % 4 != 0:
            continue
        try:
            raw = base64.b64decode(candidate, validate=True)
            if not raw or len(raw) > _MAX_DECODED_BYTES:
                continue
            nested = raw.decode("utf-8")
        except (binascii.Error, UnicodeError, ValueError):
            continue
        if _contains_sensitive_value(nested, depth=depth + 1):
            return True
    return False


__all__ = ("contains_sensitive_text",)
