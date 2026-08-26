import re
from typing import Final
from urllib.parse import parse_qsl

from trading_agent.browser_observation_redaction import contains_browser_secret_text

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
        "authsecret",
        "secret",
        "jwt",
    }
)
_KEY_SEPARATORS: Final = re.compile(r"[^a-z0-9]+")


def has_sensitive_browser_query_metadata(query: str) -> bool:
    return any(
        _normalized_key(name) in _SENSITIVE_QUERY_KEYS or contains_browser_secret_text(value)
        for name, value in parse_qsl(query, keep_blank_values=True)
    )


def _normalized_key(value: str) -> str:
    return _KEY_SEPARATORS.sub("", value.lower())


__all__ = ["has_sensitive_browser_query_metadata"]
