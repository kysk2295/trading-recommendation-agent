from __future__ import annotations

import datetime as dt
import re
from typing import override

from trading_agent.kr_keyword_research_extraction import (
    KrKeywordResearchExtractionError,
    extract_kr_keyword_research_claim,
)
from trading_agent.kr_source_collection_models import (
    KrCatalystObservationReceipt,
    KrSourceCollectionRun,
)
from trading_agent.kr_theme_keyword import (
    InvalidKrKeywordClassificationError,
    KrKeywordRuleSet,
    classify_kr_keyword_catalyst,
)
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
    KrThemeClassification,
    KrThemeDirection,
)
from trading_agent.kr_theme_store import KrThemeReader, StoredKrCatalyst
from trading_agent.research_evidence_models import ResearchEvidenceReadModel
from trading_agent.research_evidence_read_model import (
    ResearchEvidenceReadModelError,
    build_research_evidence_read_model,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ELIGIBLE_SOURCES = (KrCatalystSource.DART, KrCatalystSource.NEWS)


class KrKeywordResearchProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR keyword research projection is blocked"


def project_kr_keyword_research_evidence(
    reader: KrThemeReader,
    *,
    collection_cycle_id: str,
    classification_run_id: str,
    rules: KrKeywordRuleSet,
    classified_at: dt.datetime,
    as_of: dt.datetime,
) -> ResearchEvidenceReadModel | None:
    try:
        _validate_request(
            reader,
            collection_cycle_id,
            classification_run_id,
            rules,
            classified_at,
            as_of,
        )
        catalysts = reader.catalysts()
        links = reader.observation_receipts(collection_cycle_id)
        runs = reader.source_runs(collection_cycle_id)
        classifications = reader.classifications()
        selected = _selected_inputs(
            catalysts,
            links,
            runs,
            classifications,
            classification_run_id=classification_run_id,
            rules=rules,
            classified_at=classified_at,
            as_of=as_of,
        )
        pairs = tuple(
            extract_kr_keyword_research_claim(catalyst, link, classification, run)
            for catalyst, link, classification, run in selected
            if classification.direction is KrThemeDirection.POSITIVE
        )
        if not pairs:
            return None
        return build_research_evidence_read_model(
            tuple(item[0] for item in pairs),
            tuple(item[1] for item in pairs),
            as_of=as_of,
            current_window=dt.timedelta(hours=1),
            baseline_window=dt.timedelta(days=1),
            burst_threshold_bps=20_000,
        )
    except (
        InvalidKrKeywordClassificationError,
        KrKeywordResearchExtractionError,
        ResearchEvidenceReadModelError,
        TypeError,
        ValueError,
    ):
        raise KrKeywordResearchProjectionError from None


def _validate_request(
    reader: KrThemeReader,
    collection_cycle_id: str,
    classification_run_id: str,
    rules: KrKeywordRuleSet,
    classified_at: dt.datetime,
    as_of: dt.datetime,
) -> None:
    if (
        not isinstance(reader, KrThemeReader)
        or _SAFE_ID.fullmatch(collection_cycle_id) is None
        or _SAFE_ID.fullmatch(classification_run_id) is None
        or type(rules) is not KrKeywordRuleSet
        or classified_at.tzinfo is None
        or classified_at.utcoffset() is None
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
        or classified_at > as_of
    ):
        raise KrKeywordResearchProjectionError


def _selected_inputs(
    catalysts: tuple[StoredKrCatalyst, ...],
    links: tuple[KrCatalystObservationReceipt, ...],
    runs: tuple[KrSourceCollectionRun, ...],
    classifications: tuple[KrThemeClassification, ...],
    *,
    classification_run_id: str,
    rules: KrKeywordRuleSet,
    classified_at: dt.datetime,
    as_of: dt.datetime,
) -> tuple[
    tuple[
        StoredKrCatalyst,
        KrCatalystObservationReceipt,
        KrThemeClassification,
        KrSourceCollectionRun,
    ],
    ...,
]:
    catalyst_by_id = {item.record.catalyst_id: item for item in catalysts}
    eligible_links = tuple(
        item
        for item in links
        if item.catalyst_id in catalyst_by_id and catalyst_by_id[item.catalyst_id].record.source in _ELIGIBLE_SOURCES
    )
    run_by_source = {item.source: item for item in runs if item.source in _ELIGIBLE_SOURCES}
    selected_classifications = tuple(
        item for item in classifications if item.classification_run_id == classification_run_id
    )
    classification_by_catalyst = {item.catalyst_id: item for item in selected_classifications}
    if (
        len(catalyst_by_id) != len(catalysts)
        or len({item.catalyst_id for item in eligible_links}) != len(eligible_links)
        or set(run_by_source) != set(_ELIGIBLE_SOURCES)
        or len(run_by_source) != len(tuple(item for item in runs if item.source in _ELIGIBLE_SOURCES))
        or len(classification_by_catalyst) != len(selected_classifications)
        or set(classification_by_catalyst) != {item.catalyst_id for item in eligible_links}
        or any(
            item.status is not KrCoverageStatus.SUCCESS or item.completed_at > as_of for item in run_by_source.values()
        )
        or any(
            classification.classifier_version != rules.classifier_version
            or classification.prompt_version != rules.prompt_version
            or classification.classified_at != classified_at
            or classification.classified_at > as_of
            or classification.direction not in (KrThemeDirection.POSITIVE, KrThemeDirection.IRRELEVANT)
            for classification in selected_classifications
        )
        or any(
            run_by_source[source].record_count
            != sum(catalyst_by_id[item.catalyst_id].record.source is source for item in eligible_links)
            for source in _ELIGIBLE_SOURCES
        )
    ):
        raise KrKeywordResearchProjectionError
    for catalyst_id, classification in classification_by_catalyst.items():
        regenerated = classify_kr_keyword_catalyst(
            catalyst_by_id[catalyst_id],
            rules,
            classification_run_id=classification_run_id,
            classified_at=classified_at,
        )
        if regenerated != classification:
            raise KrKeywordResearchProjectionError
    return tuple(
        (
            catalyst_by_id[link.catalyst_id],
            link,
            classification_by_catalyst[link.catalyst_id],
            run_by_source[catalyst_by_id[link.catalyst_id].record.source],
        )
        for link in sorted(eligible_links, key=lambda item: item.catalyst_id)
    )


__all__ = (
    "KrKeywordResearchProjectionError",
    "project_kr_keyword_research_evidence",
)
