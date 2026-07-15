from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrClassifierKind,
    KrRelatedSymbol,
    KrThemeClassification,
    KrThemeDirection,
)
from trading_agent.kr_theme_store import StoredKrCatalyst

SUPPORTED_TEXT_FIELDS: Final = (
    "title",
    "body",
    "summary",
    "report_name",
    "company_name",
)
ELIGIBLE_SOURCES: Final = frozenset(
    {KrCatalystSource.NEWS, KrCatalystSource.DART}
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InvalidKrKeywordClassificationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR keyword theme classification 입력이 유효하지 않습니다"


class KrKeywordRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    theme_name: str
    keywords: tuple[str, ...]
    related_symbols: tuple[KrRelatedSymbol, ...]

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        keyword_keys = tuple(keyword.casefold() for keyword in self.keywords)
        symbols = tuple(item.symbol for item in self.related_symbols)
        if (
            not _canonical_text(self.theme_name, max_length=128)
            or not self.keywords
            or any(not _canonical_text(keyword, max_length=100) for keyword in self.keywords)
            or keyword_keys != tuple(sorted(set(keyword_keys)))
            or not self.related_symbols
            or symbols != tuple(sorted(set(symbols)))
        ):
            raise ValueError("invalid KR keyword rule")
        return self


class KrKeywordRuleSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    classifier_version: str
    prompt_version: str
    rules: tuple[KrKeywordRule, ...]

    @model_validator(mode="after")
    def validate_rule_set(self) -> Self:
        theme_names = tuple(rule.theme_name for rule in self.rules)
        if (
            _SAFE_ID.fullmatch(self.classifier_version) is None
            or _SAFE_ID.fullmatch(self.prompt_version) is None
            or not self.rules
            or theme_names != tuple(sorted(set(theme_names)))
        ):
            raise ValueError("invalid KR keyword rule set")
        return self


@dataclass(frozen=True, slots=True)
class _ExtractedText:
    fields: tuple[tuple[str, str], ...] = field(repr=False)


def classify_kr_keyword_catalyst(
    catalyst: StoredKrCatalyst,
    rules: KrKeywordRuleSet,
    *,
    classification_run_id: str,
    classified_at: dt.datetime,
) -> KrThemeClassification:
    try:
        if (
            catalyst.record.source not in ELIGIBLE_SOURCES
            or catalyst.record.content_type != "application/json"
            or hashlib.sha256(catalyst.raw_payload).hexdigest()
            != catalyst.record.payload_sha256
            or not _aware(classified_at)
            or classified_at < catalyst.record.first_observed_at
        ):
            raise InvalidKrKeywordClassificationError
        extracted = _extract_text(catalyst.raw_payload)
        matching_rules = tuple(
            rule
            for rule in rules.rules
            if _rule_matches(rule, extracted)
        )
        if len(matching_rules) > 1:
            raise InvalidKrKeywordClassificationError
        if matching_rules:
            rule = matching_rules[0]
            direction = KrThemeDirection.POSITIVE
            theme_name: str | None = rule.theme_name
            related_symbols = rule.related_symbols
            evidence_quote = _matching_quote(rule, extracted)
        else:
            direction = KrThemeDirection.IRRELEVANT
            theme_name = None
            related_symbols = ()
            evidence_quote = _bounded_quote(extracted.fields[0][1])
        return KrThemeClassification(
            catalyst_id=catalyst.record.catalyst_id,
            classifier_kind=KrClassifierKind.KEYWORD,
            classifier_version=rules.classifier_version,
            prompt_version=rules.prompt_version,
            classification_run_id=classification_run_id,
            classified_at=classified_at,
            direction=direction,
            confidence=Decimal(1),
            evidence_quote=evidence_quote,
            theme_name=theme_name,
            related_symbols=related_symbols,
        )
    except InvalidKrKeywordClassificationError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValidationError, ValueError):
        raise InvalidKrKeywordClassificationError from None


def _extract_text(raw_payload: bytes) -> _ExtractedText:
    try:
        document: object = json.loads(raw_payload)
    except (UnicodeError, json.JSONDecodeError):
        raise InvalidKrKeywordClassificationError from None
    if not isinstance(document, dict):
        raise InvalidKrKeywordClassificationError

    fields: list[tuple[str, str]] = []
    for name in SUPPORTED_TEXT_FIELDS:
        if name not in document:
            continue
        value = document[name]
        if not isinstance(value, str) or not _canonical_text(value, max_length=100_000):
            raise InvalidKrKeywordClassificationError
        fields.append((name, value))
    if not fields:
        raise InvalidKrKeywordClassificationError
    return _ExtractedText(tuple(fields))


def _rule_matches(rule: KrKeywordRule, extracted: _ExtractedText) -> bool:
    normalized_fields = tuple(value.casefold() for _, value in extracted.fields)
    return any(
        keyword.casefold() in value
        for keyword in rule.keywords
        for value in normalized_fields
    )


def _matching_quote(rule: KrKeywordRule, extracted: _ExtractedText) -> str:
    for _, value in extracted.fields:
        normalized = value.casefold()
        if any(keyword.casefold() in normalized for keyword in rule.keywords):
            return _bounded_quote(value)
    raise InvalidKrKeywordClassificationError


def _bounded_quote(value: str) -> str:
    quote = value[:200].strip()
    if not quote:
        raise InvalidKrKeywordClassificationError
    return quote


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )
