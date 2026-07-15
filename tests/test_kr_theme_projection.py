from __future__ import annotations

import datetime as dt
import hashlib
import json
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
from trading_agent.kr_theme_projection import (
    InvalidKrThemeProjectionError,
    KrVolumeSurgePayload,
    KrVolumeSurgeSymbol,
    project_kr_theme_opportunities,
)
from trading_agent.kr_theme_store import StoredKrCatalyst
from trading_agent.research_identity_models import AgentFamily, MarketId

KST = dt.timezone(dt.timedelta(hours=9))
CYCLE_START = dt.datetime(2026, 7, 15, 9, 0, tzinfo=KST)
NEWS_AT = CYCLE_START + dt.timedelta(seconds=30)
SECOND_NEWS_AT = CYCLE_START + dt.timedelta(seconds=45)
VOLUME_AT = CYCLE_START + dt.timedelta(minutes=1, seconds=30)
CYCLE_COMPLETE = CYCLE_START + dt.timedelta(minutes=2)
PROJECTED_AT = CYCLE_START + dt.timedelta(minutes=3)
CLASSIFIED_AT = CYCLE_COMPLETE + dt.timedelta(seconds=10)


def test_volume_surge_payload_requires_canonical_finite_symbols() -> None:
    valid = KrVolumeSurgePayload(
        observed_at=VOLUME_AT,
        symbols=(
            KrVolumeSurgeSymbol(
                symbol="005930",
                trading_value_krw=Decimal("100"),
                volume_ratio=Decimal("2.5"),
            ),
            KrVolumeSurgeSymbol(
                symbol="012345",
                trading_value_krw=Decimal("200"),
                volume_ratio=Decimal("3"),
            ),
        ),
    )

    assert valid.symbols[0].symbol == "005930"
    with pytest.raises(ValidationError):
        _ = KrVolumeSurgePayload.model_validate(
            valid.model_dump(mode="python")
            | {"symbols": tuple(reversed(valid.symbols))}
        )
    with pytest.raises(ValidationError):
        _ = KrVolumeSurgeSymbol(
            symbol="005930",
            trading_value_krw=Decimal("-1"),
            volume_ratio=Decimal("2"),
        )
    with pytest.raises(ValidationError):
        _ = KrVolumeSurgePayload.model_validate(
            valid.model_dump(mode="python")
            | {"observed_at": VOLUME_AT.replace(tzinfo=None)}
        )


def test_projection_builds_deterministic_theme_state_and_kr_opportunity() -> None:
    news = _news("001", NEWS_AT)
    volume = _volume(
        (
            ("005930", "100", "2.5"),
            ("012345", "200", "3"),
        )
    )
    catalysts = (news, volume)
    observations = _observations(catalysts)
    cycle = _cycle(catalysts)
    primary = _classification(news, symbols=("005930", "012345"))
    other_run = _classification(
        news,
        symbols=("005930", "012345"),
        run_id="other-run",
    )

    first = _project(cycle, catalysts, observations, (primary, other_run))
    second = _project(cycle, catalysts, observations, (other_run, primary))

    assert len(first) == 1
    state = first[0].state
    opportunity = first[0].opportunity
    assert state.theme_name == "반도체"
    assert state.freshness_seconds == 150
    assert state.catalyst_count == 1
    assert state.publisher_count == 1
    assert state.total_trading_value_krw == Decimal("300")
    assert state.leader_symbol == "012345"
    assert tuple(item.symbol for item in state.related_symbols) == ("012345", "005930")
    assert opportunity.strategy_lane.market_id is MarketId.KR_EQUITIES
    assert opportunity.strategy_lane.agent_family is AgentFamily.OPPORTUNITY_MANAGER
    assert opportunity.strategy_lane.strategy_id == "theme_momentum"
    assert tuple(item.symbol for item in opportunity.candidates) == ("012345", "005930")
    assert tuple(item.rank for item in opportunity.candidates) == (1, 2)
    assert tuple(item.score for item in opportunity.candidates) == (
        Decimal("200"),
        Decimal("100"),
    )
    assert tuple(feature.name for feature in opportunity.candidates[0].features) == tuple(
        sorted(feature.name for feature in opportunity.candidates[0].features)
    )
    assert len(opportunity.source_coverage) == 4
    assert all(item.complete for item in opportunity.source_coverage)
    assert {item.namespace for item in opportunity.evidence_refs} == {
        "kr/catalyst/volume_surge",
        "kr/collection_cycle",
        "kr/theme_classification",
    }
    assert first[0].state.model_dump(mode="json") == second[0].state.model_dump(mode="json")
    assert first[0].opportunity.model_dump(mode="json") == second[0].opportunity.model_dump(
        mode="json"
    )


def test_projection_aggregates_same_theme_and_uses_symbol_tie_break() -> None:
    first_news = _news("001", NEWS_AT, publisher="same_publisher")
    second_news = _news("002", SECOND_NEWS_AT, publisher="same_publisher")
    volume = _volume(
        (
            ("005930", "100", "2.5"),
            ("012345", "100", "3"),
        )
    )
    catalysts = (first_news, second_news, volume)
    classifications = (
        _classification(first_news, symbols=("005930", "012345")),
        _classification(second_news, symbols=("005930", "012345")),
    )

    result = _project(
        _cycle(catalysts),
        catalysts,
        _observations(catalysts),
        classifications,
    )

    state = result[0].state
    assert state.catalyst_count == 2
    assert state.publisher_count == 1
    assert state.first_observed_at == NEWS_AT
    assert state.latest_observed_at == SECOND_NEWS_AT
    assert state.leader_symbol == "005930"
    assert tuple(item.symbol for item in state.related_symbols) == ("005930", "012345")


def test_projection_requires_complete_cycle() -> None:
    news = _news("001", NEWS_AT)
    volume = _volume((("005930", "100", "2"),))
    catalysts = (news, volume)

    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(catalysts, failed_source=KrCatalystSource.DART),
            catalysts,
            _observations(catalysts),
            (_classification(news),),
        )


def test_projection_requires_one_exact_cohort_classification_per_text_catalyst() -> None:
    news = _news("001", NEWS_AT)
    volume = _volume((("005930", "100", "2"),))
    catalysts = (news, volume)
    wrong_version = _classification(news, classifier_version="kr-keyword-other-v1")

    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(catalysts),
            catalysts,
            _observations(catalysts),
            (wrong_version,),
        )
    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(catalysts),
            catalysts,
            _observations(catalysts),
            (_classification(news), _classification(news)),
        )


def test_projection_keeps_irrelevant_classification_as_coverage_without_theme() -> None:
    news = _news("001", NEWS_AT)
    volume = _volume((("005930", "100", "2"),))
    catalysts = (news, volume)

    result = _project(
        _cycle(catalysts),
        catalysts,
        _observations(catalysts),
        (_classification(news, direction=KrThemeDirection.IRRELEVANT),),
    )

    assert result == ()


def test_projection_rejects_missing_or_duplicate_volume_metrics() -> None:
    news = _news("001", NEWS_AT)
    missing_volume = _volume((("005930", "100", "2"),))
    missing_catalysts = (news, missing_volume)
    classification = _classification(news, symbols=("005930", "012345"))

    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(missing_catalysts),
            missing_catalysts,
            _observations(missing_catalysts),
            (classification,),
        )

    duplicate_a = _volume((("005930", "100", "2"),))
    duplicate_b = _volume(
        (("005930", "100", "2"),),
        suffix="002",
        observed_at=VOLUME_AT + dt.timedelta(seconds=1),
    )
    duplicate_catalysts = (news, duplicate_a, duplicate_b)
    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(duplicate_catalysts),
            duplicate_catalysts,
            _observations(duplicate_catalysts),
            (_classification(news),),
        )


def test_projection_rejects_future_or_misaligned_evidence() -> None:
    news = _news("001", NEWS_AT)
    volume = _volume((("005930", "100", "2"),))
    catalysts = (news, volume)

    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(catalysts),
            catalysts,
            _observations(catalysts),
            (_classification(news, classified_at=PROJECTED_AT + dt.timedelta(seconds=1)),),
        )

    payload_time = VOLUME_AT + dt.timedelta(seconds=1)
    misaligned_volume = _volume(
        (("005930", "100", "2"),),
        payload_observed_at=payload_time,
    )
    misaligned = (news, misaligned_volume)
    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(misaligned),
            misaligned,
            _observations(misaligned),
            (_classification(news),),
        )


def test_projection_rejects_observation_outside_final_cycle_window() -> None:
    late_news_at = CYCLE_COMPLETE + dt.timedelta(seconds=1)
    news = _news("001", late_news_at)
    volume = _volume((("005930", "100", "2"),))
    catalysts = (news, volume)

    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(catalysts),
            catalysts,
            _observations(catalysts),
            (_classification(news, classified_at=late_news_at),),
        )


def test_projection_rejects_unverified_raw_checksum() -> None:
    news = _news("001", NEWS_AT)
    original_volume = _volume((("005930", "100", "2"),))
    changed_volume = _volume((("005930", "999", "2"),))
    tampered_volume = StoredKrCatalyst(
        record=original_volume.record,
        raw_payload=changed_volume.raw_payload,
    )
    catalysts = (news, tampered_volume)

    with pytest.raises(InvalidKrThemeProjectionError):
        _ = _project(
            _cycle(catalysts),
            catalysts,
            _observations(catalysts),
            (_classification(news),),
        )


def test_projection_error_does_not_render_raw_payload() -> None:
    private_text = "private-volume-payload"
    news = _news("001", NEWS_AT)
    invalid_volume = _raw_stored(
        source=KrCatalystSource.VOLUME_SURGE,
        suffix="001",
        observed_at=VOLUME_AT,
        payload=f'{{"private":"{private_text}"}}'.encode(),
    )
    catalysts = (news, invalid_volume)

    with pytest.raises(InvalidKrThemeProjectionError) as captured:
        _ = _project(
            _cycle(catalysts),
            catalysts,
            _observations(catalysts),
            (_classification(news),),
        )

    assert private_text not in str(captured.value)
    assert private_text not in repr(captured.value)
    assert captured.value.__cause__ is None


def _project(
    cycle: KrCatalystCollectionCycle,
    catalysts: tuple[StoredKrCatalyst, ...],
    observations: tuple[KrCatalystObservation, ...],
    classifications: tuple[KrThemeClassification, ...],
):
    return project_kr_theme_opportunities(
        cycle,
        catalysts,
        observations,
        classifications,
        classifier_version="kr-keyword-synthetic-v1",
        prompt_version="no-prompt-v1",
        classification_run_id="kr-keyword-run-001",
        projected_at=PROJECTED_AT,
        validity=dt.timedelta(minutes=10),
        producer_strategy_version="kr-theme-keyword-projection-v1",
    )


def _classification(
    catalyst: StoredKrCatalyst,
    *,
    symbols: tuple[str, ...] = ("005930",),
    direction: KrThemeDirection = KrThemeDirection.POSITIVE,
    classifier_version: str = "kr-keyword-synthetic-v1",
    run_id: str = "kr-keyword-run-001",
    classified_at: dt.datetime = CLASSIFIED_AT,
) -> KrThemeClassification:
    positive = direction is KrThemeDirection.POSITIVE
    return KrThemeClassification(
        catalyst_id=catalyst.record.catalyst_id,
        classifier_kind=KrClassifierKind.KEYWORD,
        classifier_version=classifier_version,
        prompt_version="no-prompt-v1",
        classification_run_id=run_id,
        classified_at=classified_at,
        direction=direction,
        confidence=Decimal(1),
        evidence_quote="합성 반도체 촉매",
        theme_name="반도체" if positive else None,
        related_symbols=(
            tuple(
                KrRelatedSymbol(
                    symbol=symbol,
                    relation=KrThemeRelation.DIRECT_BUSINESS,
                    rationale="합성 rule 관련 종목",
                )
                for symbol in symbols
            )
            if positive
            else ()
        ),
    )


def _news(
    suffix: str,
    observed_at: dt.datetime,
    *,
    publisher: str = "synthetic_news",
) -> StoredKrCatalyst:
    return _raw_stored(
        source=KrCatalystSource.NEWS,
        suffix=suffix,
        observed_at=observed_at,
        payload=f'{{"title":"합성 반도체 촉매 {suffix}"}}'.encode(),
        publisher=publisher,
    )


def _volume(
    metrics: tuple[tuple[str, str, str], ...],
    *,
    suffix: str = "001",
    observed_at: dt.datetime = VOLUME_AT,
    payload_observed_at: dt.datetime | None = None,
) -> StoredKrCatalyst:
    payload = {
        "schema_version": 1,
        "observed_at": (payload_observed_at or observed_at).isoformat(),
        "symbols": [
            {
                "schema_version": 1,
                "symbol": symbol,
                "trading_value_krw": trading_value,
                "volume_ratio": volume_ratio,
            }
            for symbol, trading_value, volume_ratio in metrics
        ],
    }
    return _raw_stored(
        source=KrCatalystSource.VOLUME_SURGE,
        suffix=suffix,
        observed_at=observed_at,
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        publisher="derived_volume_surge",
    )


def _raw_stored(
    *,
    source: KrCatalystSource,
    suffix: str,
    observed_at: dt.datetime,
    payload: bytes,
    publisher: str = "synthetic",
) -> StoredKrCatalyst:
    record = KrCatalystRecord(
        source=source,
        source_record_id=f"{source.value}://synthetic/{suffix}",
        publisher_id=publisher,
        published_at=None,
        first_observed_at=observed_at,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return StoredKrCatalyst(record=record, raw_payload=payload)


def _observations(
    catalysts: tuple[StoredKrCatalyst, ...],
) -> tuple[KrCatalystObservation, ...]:
    return tuple(
        KrCatalystObservation(
            collection_cycle_id="kr-theme-projection-001",
            catalyst_id=item.record.catalyst_id,
            observed_at=item.record.first_observed_at,
        )
        for item in catalysts
    )


def _cycle(
    catalysts: tuple[StoredKrCatalyst, ...],
    *,
    failed_source: KrCatalystSource | None = None,
) -> KrCatalystCollectionCycle:
    counts = {
        source: sum(item.record.source is source for item in catalysts)
        for source in KrCatalystSource
    }
    return KrCatalystCollectionCycle(
        collection_cycle_id="kr-theme-projection-001",
        started_at=CYCLE_START,
        completed_at=CYCLE_COMPLETE,
        coverage=tuple(
            KrSourceCoverage(
                source=source,
                status=(
                    KrCoverageStatus.FAILED
                    if source is failed_source
                    else KrCoverageStatus.SUCCESS
                ),
                record_count=counts[source],
                failure_code="synthetic_failure" if source is failed_source else None,
            )
            for source in sorted(KrCatalystSource, key=lambda item: item.value)
        ),
    )
