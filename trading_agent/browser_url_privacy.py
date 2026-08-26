import re
from typing import Final
from urllib.parse import parse_qsl

_SENSITIVE_QUERY_KEYS: Final = frozenset(
    {
        "authorization",
        "accesstoken",
        "accesskey",
        "refreshtoken",
        "refreshkey",
        "apikey",
        "apisecret",
        "apitoken",
        "session",
        "sessionid",
        "sessionkey",
        "sessiontoken",
        "cookie",
        "password",
        "clientsecret",
        "accountid",
        "accountnumber",
        "token",
        "authtoken",
    }
)
_KEY_SEPARATORS: Final = re.compile(r"[^a-z0-9]+")
_LABEL: Final = (
    r"(?:authorization|access[\s_-]*(?:key|token)|refresh[\s_-]*(?:key|token)|"
    r"api[\s_-]*(?:key|secret|token)|session(?:[\s_-]*(?:cookie|id|key|token))?|"
    r"cookie|password|client[\s_-]*secret|account[\s_-]*(?:id|number)|auth[\s_-]*token|token|secret|jwt)"
)
_ASSIGNED_SECRET: Final = re.compile(rf"\b{_LABEL}\b\s*[:=]\s*[^\s,;]+", flags=re.IGNORECASE)
_BEARER_SECRET: Final = re.compile(r"\bbearer\s+[^\s,;]+", flags=re.IGNORECASE)
_JWT_SECRET: Final = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)


def has_sensitive_browser_query_metadata(query: str) -> bool:
    return any(
        _normalized_key(name) in _SENSITIVE_QUERY_KEYS or _contains_secret(value)
        for name, value in parse_qsl(query, keep_blank_values=True)
    )


def _normalized_key(value: str) -> str:
    return _KEY_SEPARATORS.sub("", value.lower())


def _contains_secret(value: str) -> bool:
    return bool(_ASSIGNED_SECRET.search(value) or _BEARER_SECRET.search(value) or _JWT_SECRET.search(value))


__all__ = ["has_sensitive_browser_query_metadata"]
