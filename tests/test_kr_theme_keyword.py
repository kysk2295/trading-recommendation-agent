from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.kr_theme_keyword import (
    InvalidKrKeywordClassificationError,
    KrKeywordRule,
    KrKeywordRuleSet,
    classify_kr_keyword_catalyst,
)
from trading_agent.kr_theme_models import (
    KrCatalystRecord,
    KrCatalystSource,
    KrRelatedSymbol,
    KrThemeDirection,
    KrThemeRelation,
)
from trading_agent.kr_theme_store import StoredKrCatalyst

OBSERVED_AT = dt.datetime(2026, 7, 15, 9, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=9)))
CLASSIFIED_AT = OBSERVED_AT + dt.timedelta(seconds=20)


def test_keyword_rules_require_canonical_unique_order() -> None:
    rules = _rules()

    assert rules.classifier_version == "kr-keyword-synthetic-v1"
    assert rules.rules[0].keywords == ("공급망", "반도체")

    with pytest.raises(ValidationError):
        _ = KrKeywordRule.model_validate(
            rules.rules[0].model_dump(mode="python")
            | {"keywords": ("반도체", "공급망")}
        )
    with pytest.raises(ValidationError):
        _ = KrKeywordRule.model_validate(
            rules.rules[0].model_dump(mode="python")
            | {"keywords": ("반도체", "반도체")}
        )
    with pytest.raises(ValidationError):
        _ = KrKeywordRuleSet.model_validate(
            rules.model_dump(mode="python")
            | {
                "rules": (
                    _rule("우주항공", keywords=("우주",), symbol="012345"),
                    rules.rules[0],
                )
            }
        )
    with pytest.raises(ValidationError):
        _ = KrKeywordRule(
            theme_name="반도체",
            keywords=("반도체",),
            related_symbols=(
                _related("123456"),
                _related("005930"),
            ),
        )


def test_keyword_classifier_builds_positive_classification_from_first_matching_field() -> None:
    payload = (
        '{"title":"합성 반도체 공급망 발표","body":"반도체 생산 확대",'
        '"publisher":"synthetic"}'
    ).encode()

    classification = classify_kr_keyword_catalyst(
        _stored(payload),
        _rules(),
        classification_run_id="kr-keyword-run-001",
        classified_at=CLASSIFIED_AT,
    )

    assert classification.direction is KrThemeDirection.POSITIVE
    assert classification.theme_name == "반도체"
    assert classification.confidence == Decimal(1)
    assert classification.evidence_quote == "합성 반도체 공급망 발표"
    assert tuple(item.symbol for item in classification.related_symbols) == ("005930",)
    assert classification.classifier_version == "kr-keyword-synthetic-v1"
    assert classification.prompt_version == "no-prompt-v1"


def test_keyword_classifier_preserves_irrelevant_result_without_theme_or_symbols() -> None:
    classification = classify_kr_keyword_catalyst(
        _stored('{"title":"합성 바이오 행사"}'.encode()),
        _rules(),
        classification_run_id="kr-keyword-run-001",
        classified_at=CLASSIFIED_AT,
    )

    assert classification.direction is KrThemeDirection.IRRELEVANT
    assert classification.theme_name is None
    assert classification.related_symbols == ()
    assert classification.evidence_quote == "합성 바이오 행사"
    assert classification.confidence == Decimal(1)


def test_keyword_classifier_rejects_ambiguous_themes_without_leaking_text() -> None:
    rules = KrKeywordRuleSet(
        classifier_version="kr-keyword-synthetic-v1",
        prompt_version="no-prompt-v1",
        rules=(
            _rule("반도체", keywords=("반도체",), symbol="005930"),
            _rule("우주항공", keywords=("우주",), symbol="012345"),
        ),
    )
    private_text = "합성 반도체 우주 공동 프로젝트"

    with pytest.raises(InvalidKrKeywordClassificationError) as captured:
        _ = classify_kr_keyword_catalyst(
            _stored(f'{{"title":"{private_text}"}}'.encode()),
            rules,
            classification_run_id="kr-keyword-run-001",
            classified_at=CLASSIFIED_AT,
        )

    rendered = str(captured.value)
    assert private_text not in rendered
    assert "005930" not in rendered
    assert "news://synthetic/001" not in rendered


@pytest.mark.parametrize(
    ("payload", "source", "content_type"),
    [
        (b"not-json", KrCatalystSource.NEWS, "application/json"),
        (b"[]", KrCatalystSource.NEWS, "application/json"),
        (b'{"publisher":"synthetic"}', KrCatalystSource.NEWS, "application/json"),
        (b'{"title":1}', KrCatalystSource.NEWS, "application/json"),
        (b'{"title":""}', KrCatalystSource.NEWS, "application/json"),
        (b'{"title":"bad\\ntext"}', KrCatalystSource.NEWS, "application/json"),
        (b'{"title":"synthetic"}', KrCatalystSource.KIS_RANKING, "application/json"),
        (b"synthetic", KrCatalystSource.NEWS, "text/plain"),
    ],
)
def test_keyword_classifier_rejects_unsupported_payloads_safely(
    payload: bytes,
    source: KrCatalystSource,
    content_type: str,
) -> None:
    catalyst = _stored(payload, source=source, content_type=content_type)

    with pytest.raises(InvalidKrKeywordClassificationError) as captured:
        _ = classify_kr_keyword_catalyst(
            catalyst,
            _rules(),
            classification_run_id="kr-keyword-run-001",
            classified_at=CLASSIFIED_AT,
        )

    assert str(captured.value) == "KR keyword theme classification 입력이 유효하지 않습니다"
    assert payload.decode(errors="ignore") not in str(captured.value)


def test_keyword_classifier_bounds_evidence_and_is_deterministic() -> None:
    private_tail = "private-tail-that-must-not-appear"
    body = f"반도체 {'가' * 240}{private_tail}"
    catalyst = _stored(f'{{"body":"{body}"}}'.encode())

    first = classify_kr_keyword_catalyst(
        catalyst,
        _rules(),
        classification_run_id="kr-keyword-run-001",
        classified_at=CLASSIFIED_AT,
    )
    second = classify_kr_keyword_catalyst(
        catalyst,
        _rules(),
        classification_run_id="kr-keyword-run-001",
        classified_at=CLASSIFIED_AT,
    )

    assert len(first.evidence_quote) == 200
    assert private_tail not in first.evidence_quote
    assert private_tail not in repr(first)
    assert first == second
    assert first.classification_id == second.classification_id


def test_keyword_classifier_rejects_pre_observation_classification_time() -> None:
    with pytest.raises(InvalidKrKeywordClassificationError):
        _ = classify_kr_keyword_catalyst(
            _stored('{"title":"합성 반도체 발표"}'.encode()),
            _rules(),
            classification_run_id="kr-keyword-run-001",
            classified_at=OBSERVED_AT - dt.timedelta(microseconds=1),
        )


def test_keyword_classifier_rejects_unverified_raw_checksum() -> None:
    original = _stored('{"title":"합성 반도체 발표"}'.encode())
    changed = _stored('{"title":"합성 바이오 발표"}'.encode())
    tampered = StoredKrCatalyst(
        record=original.record,
        raw_payload=changed.raw_payload,
    )

    with pytest.raises(InvalidKrKeywordClassificationError):
        _ = classify_kr_keyword_catalyst(
            tampered,
            _rules(),
            classification_run_id="kr-keyword-run-001",
            classified_at=CLASSIFIED_AT,
        )


def test_keyword_classifier_does_not_chain_raw_parse_error() -> None:
    with pytest.raises(InvalidKrKeywordClassificationError) as captured:
        _ = classify_kr_keyword_catalyst(
            _stored(b"private-invalid-json"),
            _rules(),
            classification_run_id="kr-keyword-run-001",
            classified_at=CLASSIFIED_AT,
        )

    assert captured.value.__cause__ is None


def _rules() -> KrKeywordRuleSet:
    return KrKeywordRuleSet(
        classifier_version="kr-keyword-synthetic-v1",
        prompt_version="no-prompt-v1",
        rules=(
            _rule(
                "반도체",
                keywords=("공급망", "반도체"),
                symbol="005930",
            ),
        ),
    )


def _rule(
    theme_name: str,
    *,
    keywords: tuple[str, ...],
    symbol: str,
) -> KrKeywordRule:
    return KrKeywordRule(
        theme_name=theme_name,
        keywords=keywords,
        related_symbols=(_related(symbol),),
    )


def _related(symbol: str) -> KrRelatedSymbol:
    return KrRelatedSymbol(
        symbol=symbol,
        relation=KrThemeRelation.DIRECT_BUSINESS,
        rationale="합성 keyword rule 직접 연결",
    )


def _stored(
    payload: bytes,
    *,
    source: KrCatalystSource = KrCatalystSource.NEWS,
    content_type: str = "application/json",
) -> StoredKrCatalyst:
    record = KrCatalystRecord(
        source=source,
        source_record_id="news://synthetic/001",
        publisher_id="synthetic_news",
        published_at=OBSERVED_AT - dt.timedelta(seconds=30),
        first_observed_at=OBSERVED_AT,
        content_type=content_type,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return StoredKrCatalyst(record=record, raw_payload=payload)
