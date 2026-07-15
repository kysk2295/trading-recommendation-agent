from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrClassifierKind,
    KrCoverageStatus,
    KrRelatedSymbol,
    KrSourceCoverage,
    KrThemeClassification,
    KrThemeDirection,
    KrThemeRelation,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def test_catalyst_identity_is_deterministic_without_raw_payload() -> None:
    first = _catalyst()
    second = _catalyst()
    other_source = _catalyst(source=KrCatalystSource.DART)

    assert first.catalyst_id == second.catalyst_id
    assert len(first.catalyst_id) == 64
    assert first.catalyst_id != other_source.catalyst_id
    assert "raw_payload" not in repr(first).lower()
    assert "synthetic body" not in repr(first).lower()
    assert first.model_dump(mode="json")["payload_sha256"] == "a" * 64


def test_catalyst_rejects_invalid_time_hash_or_control_text() -> None:
    with pytest.raises(ValidationError):
        _catalyst(published_at=OBSERVED_AT + dt.timedelta(seconds=1))
    with pytest.raises(ValidationError):
        _catalyst(first_observed_at=OBSERVED_AT.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _catalyst(payload_sha256="not-a-sha")
    with pytest.raises(ValidationError):
        _catalyst(source_record_id="bad\nrecord")


def test_observation_requires_a_safe_cycle_and_catalyst_identity() -> None:
    observation = KrCatalystObservation(
        collection_cycle_id="kr-cycle-20260715-001",
        catalyst_id=_catalyst().catalyst_id,
        observed_at=OBSERVED_AT,
    )
    assert observation.collection_cycle_id == "kr-cycle-20260715-001"

    with pytest.raises(ValidationError):
        KrCatalystObservation(
            collection_cycle_id="../escape",
            catalyst_id=_catalyst().catalyst_id,
            observed_at=OBSERVED_AT,
        )


def test_cycle_requires_exact_canonical_source_coverage() -> None:
    complete = _cycle(_coverage())
    partial = _cycle(
        _coverage(
            failed_source=KrCatalystSource.DART,
            failure_code="http_503",
        )
    )

    assert complete.complete is True
    assert partial.complete is False
    assert tuple(item.source.value for item in complete.coverage) == (
        "dart",
        "kis_ranking",
        "news",
        "volume_surge",
    )

    with pytest.raises(ValidationError):
        _cycle(_coverage()[:-1])
    with pytest.raises(ValidationError):
        _cycle(tuple(reversed(_coverage())))


def test_source_coverage_status_and_failure_code_are_consistent() -> None:
    with pytest.raises(ValidationError):
        KrSourceCoverage(
            source=KrCatalystSource.NEWS,
            status=KrCoverageStatus.SUCCESS,
            record_count=1,
            failure_code="unexpected",
        )
    with pytest.raises(ValidationError):
        KrSourceCoverage(
            source=KrCatalystSource.NEWS,
            status=KrCoverageStatus.FAILED,
            record_count=0,
        )


def test_non_irrelevant_classification_requires_sorted_kr_symbols() -> None:
    classification = _classification()

    assert classification.theme_name == "반도체"
    assert tuple(item.symbol for item in classification.related_symbols) == (
        "000660",
        "005930",
    )
    assert classification.direction is KrThemeDirection.POSITIVE

    with pytest.raises(ValidationError):
        _classification(related_symbols=tuple(reversed(classification.related_symbols)))
    with pytest.raises(ValidationError):
        _classification(
            related_symbols=(
                KrRelatedSymbol(
                    symbol="AAPL",
                    relation=KrThemeRelation.DIRECT_BUSINESS,
                    rationale="한국 종목코드가 아님",
                ),
            )
        )


def test_irrelevant_classification_has_no_theme_or_symbols() -> None:
    irrelevant = _classification(
        direction=KrThemeDirection.IRRELEVANT,
        confidence=Decimal("0.95"),
        theme_name=None,
        related_symbols=(),
    )
    assert irrelevant.theme_name is None

    with pytest.raises(ValidationError):
        _classification(
            direction=KrThemeDirection.IRRELEVANT,
            theme_name="반도체",
        )
    with pytest.raises(ValidationError):
        _classification(theme_name=None, related_symbols=())


def test_classification_run_is_part_of_the_immutable_identity() -> None:
    primary = _classification(classification_run_id="primary")
    repeat = _classification(classification_run_id="stability-001")

    assert len(primary.classification_id) == 64
    assert primary.classification_id != repeat.classification_id
    assert _classification(classification_run_id="primary").classification_id == primary.classification_id

    with pytest.raises(ValidationError):
        _classification(confidence=Decimal("1.01"))
    with pytest.raises(ValidationError):
        _classification(classified_at=OBSERVED_AT.replace(tzinfo=None))


def _catalyst(
    *,
    source: KrCatalystSource = KrCatalystSource.NEWS,
    source_record_id: str = "news://synthetic/001",
    published_at: dt.datetime | None = OBSERVED_AT - dt.timedelta(minutes=1),
    first_observed_at: dt.datetime = OBSERVED_AT,
    payload_sha256: str = "a" * 64,
) -> KrCatalystRecord:
    return KrCatalystRecord(
        source=source,
        source_record_id=source_record_id,
        publisher_id="synthetic_news",
        published_at=published_at,
        first_observed_at=first_observed_at,
        content_type="application/json",
        payload_sha256=payload_sha256,
    )


def _coverage(
    *,
    failed_source: KrCatalystSource | None = None,
    failure_code: str | None = None,
) -> tuple[KrSourceCoverage, ...]:
    return tuple(
        KrSourceCoverage(
            source=source,
            status=(
                KrCoverageStatus.FAILED
                if source is failed_source
                else KrCoverageStatus.SUCCESS
            ),
            record_count=0 if source is failed_source else 1,
            failure_code=failure_code if source is failed_source else None,
        )
        for source in sorted(KrCatalystSource, key=lambda item: item.value)
    )


def _cycle(coverage: tuple[KrSourceCoverage, ...]) -> KrCatalystCollectionCycle:
    return KrCatalystCollectionCycle(
        collection_cycle_id="kr-cycle-20260715-001",
        started_at=OBSERVED_AT - dt.timedelta(minutes=2),
        completed_at=OBSERVED_AT + dt.timedelta(minutes=1),
        coverage=coverage,
    )


def _classification(
    *,
    classification_run_id: str = "primary",
    classified_at: dt.datetime = OBSERVED_AT + dt.timedelta(seconds=10),
    direction: KrThemeDirection = KrThemeDirection.POSITIVE,
    confidence: Decimal = Decimal("0.9"),
    theme_name: str | None = "반도체",
    related_symbols: tuple[KrRelatedSymbol, ...] | None = None,
) -> KrThemeClassification:
    symbols = (
        (
            KrRelatedSymbol(
                symbol="000660",
                relation=KrThemeRelation.DIRECT_BUSINESS,
                rationale="합성 기사에 직접 언급된 반도체 사업",
            ),
            KrRelatedSymbol(
                symbol="005930",
                relation=KrThemeRelation.SUPPLY_CHAIN,
                rationale="합성 기사에 언급된 공급망 관계",
            ),
        )
        if related_symbols is None
        else related_symbols
    )
    return KrThemeClassification(
        catalyst_id=_catalyst().catalyst_id,
        classifier_kind=KrClassifierKind.KEYWORD,
        classifier_version="kr-keyword-v1",
        prompt_version="no-prompt-v1",
        classification_run_id=classification_run_id,
        classified_at=classified_at,
        direction=direction,
        confidence=confidence,
        evidence_quote="합성 반도체 공급망 기사",
        theme_name=theme_name,
        related_symbols=symbols,
    )
