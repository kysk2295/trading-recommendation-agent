from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tests.kr_research_fixtures import (
    CLASSIFICATION_RUN_ID,
    CLASSIFIED_AT,
    CYCLE_ID,
    OBSERVED_AT,
    append_kr_research_input,
    kr_keyword_rules,
    kr_research_input,
    stored_kr_catalyst,
)
from trading_agent.kr_keyword_research_extraction import (
    KrKeywordResearchExtractionError,
    extract_kr_keyword_research_claim,
)
from trading_agent.kr_keyword_research_projection import project_kr_keyword_research_evidence
from trading_agent.kr_theme_models import KrCatalystSource, KrThemeDirection
from trading_agent.kr_theme_store import KrThemeReader, KrThemeStore, StoredKrCatalyst
from trading_agent.research_evidence_models import (
    ClaimCorroborationStatus,
    ClaimStance,
    ExtractionMethod,
)
from trading_agent.research_evidence_read_model import build_research_evidence_read_model


def test_dart_and_ls_keyword_claims_corroborate_only_on_exact_theme_entities() -> None:
    dart = kr_research_input(KrCatalystSource.DART, "a")
    news = kr_research_input(KrCatalystSource.NEWS, "b")

    dart_event, dart_claim = extract_kr_keyword_research_claim(*dart)
    news_event, news_claim = extract_kr_keyword_research_claim(*news)
    model = build_research_evidence_read_model(
        (dart_event, news_event),
        (dart_claim, news_claim),
        as_of=CLASSIFIED_AT + dt.timedelta(seconds=1),
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(days=1),
        burst_threshold_bps=20_000,
    )

    assert dart_event.source_id.canonical_id == "opendart/list"
    assert news_event.source_id.canonical_id == "ls/nws"
    assert dart_event.raw_receipt_ref == "a" * 64
    assert news_event.raw_receipt_ref == "b" * 64
    assert dart_event.normalized_at == CLASSIFIED_AT
    assert dart_event.entity_refs == news_event.entity_refs
    assert dart_claim.claim_key == news_claim.claim_key
    assert dart_claim.claim_kind == news_claim.claim_kind == "theme.catalyst"
    assert dart_claim.stance is news_claim.stance is ClaimStance.SUPPORTS
    assert dart_claim.extraction_method is ExtractionMethod.DETERMINISTIC
    assert dart_claim.output_sha256 != news_claim.output_sha256
    assert len(model.claims) == 1
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.CORROBORATED
    assert model.claims[0].independent_source_count == 2


def test_mismatched_lineage_payload_and_nonintegral_confidence_fail_closed() -> None:
    catalyst, link, classification, run = kr_research_input(KrCatalystSource.DART, "a")
    wrong_link = link.model_copy(update={"receipt_id": "c" * 64})
    wrong_classification = classification.model_copy(update={"catalyst_id": "d" * 64})
    nonintegral_confidence = classification.model_copy(update={"confidence": Decimal("0.33333")})
    news_payload = stored_kr_catalyst(KrCatalystSource.NEWS)
    wrong_payload = StoredKrCatalyst(catalyst.record, news_payload.raw_payload)

    for candidate in (
        (catalyst, wrong_link, classification, run),
        (catalyst, link, wrong_classification, run),
        (catalyst, link, nonintegral_confidence, run),
        (wrong_payload, link, classification, run),
    ):
        with pytest.raises(KrKeywordResearchExtractionError, match="blocked") as captured:
            extract_kr_keyword_research_claim(*candidate)
        assert repr(captured.value) == "KrKeywordResearchExtractionError()"


def test_irrelevant_or_preobservation_classification_does_not_create_claim() -> None:
    catalyst, link, classification, run = kr_research_input(KrCatalystSource.NEWS, "b")
    irrelevant = classification.model_copy(
        update={
            "direction": KrThemeDirection.IRRELEVANT,
            "theme_name": None,
            "related_symbols": (),
        }
    )
    preobservation = classification.model_copy(update={"classified_at": OBSERVED_AT - dt.timedelta(seconds=1)})

    for candidate in (irrelevant, preobservation):
        with pytest.raises(KrKeywordResearchExtractionError, match="blocked"):
            extract_kr_keyword_research_claim(catalyst, link, candidate, run)


def test_ledger_projection_reconciles_both_terminal_source_runs(tmp_path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    append_kr_research_input(store, KrCatalystSource.DART)
    append_kr_research_input(store, KrCatalystSource.NEWS)

    model = project_kr_keyword_research_evidence(
        KrThemeReader(store.path),
        collection_cycle_id=CYCLE_ID,
        classification_run_id=CLASSIFICATION_RUN_ID,
        rules=kr_keyword_rules(),
        classified_at=CLASSIFIED_AT,
        as_of=CLASSIFIED_AT + dt.timedelta(seconds=1),
    )

    assert model is not None
    assert model.source_event_count == 2
    assert model.extraction_count == 2
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.CORROBORATED
