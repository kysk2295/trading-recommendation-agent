from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal

from trading_agent.kr_source_collection_models import (
    KrCatalystObservationReceipt,
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_theme_keyword import (
    KrKeywordRule,
    KrKeywordRuleSet,
    classify_kr_keyword_catalyst,
)
from trading_agent.kr_theme_models import (
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrClassifierKind,
    KrCoverageStatus,
    KrRelatedSymbol,
    KrThemeClassification,
    KrThemeDirection,
    KrThemeRelation,
)
from trading_agent.kr_theme_store import KrThemeStore, StoredKrCatalyst
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION

KST = dt.timezone(dt.timedelta(hours=9))
OBSERVED_AT = dt.datetime(2026, 7, 16, 9, 2, tzinfo=KST)
CLASSIFIED_AT = OBSERVED_AT + dt.timedelta(seconds=10)
CYCLE_ID = "kr-cycle-20260716-001"
CLASSIFICATION_RUN_ID = "kr-keyword-run-20260716-001"


def kr_research_input(
    source: KrCatalystSource,
    fill: str,
) -> tuple[
    StoredKrCatalyst,
    KrCatalystObservationReceipt,
    KrThemeClassification,
    KrSourceCollectionRun,
]:
    catalyst = stored_kr_catalyst(source)
    receipt_id = fill * 64
    link = KrCatalystObservationReceipt(
        collection_cycle_id=CYCLE_ID,
        catalyst_id=catalyst.record.catalyst_id,
        receipt_id=receipt_id,
        item_index=0,
        item_payload_sha256=catalyst.record.payload_sha256,
    )
    classification = KrThemeClassification(
        catalyst_id=catalyst.record.catalyst_id,
        classifier_kind=KrClassifierKind.KEYWORD,
        classifier_version="kr-keyword-v1",
        prompt_version="no-prompt-v1",
        classification_run_id=CLASSIFICATION_RUN_ID,
        classified_at=CLASSIFIED_AT,
        direction=KrThemeDirection.POSITIVE,
        confidence=Decimal("0.91"),
        evidence_quote="Synthetic semiconductor supply contract",
        theme_name="반도체",
        related_symbols=kr_keyword_rules().rules[0].related_symbols,
    )
    run = KrSourceCollectionRun(
        source_run_id=f"{CYCLE_ID}:{source.value}",
        collection_cycle_id=CYCLE_ID,
        source=source,
        adapter_version=_adapter_version(source),
        started_at=OBSERVED_AT - dt.timedelta(seconds=1),
        completed_at=CLASSIFIED_AT,
        status=KrCoverageStatus.SUCCESS,
        record_count=1,
        receipt_ids=(receipt_id,),
        collection_date=OBSERVED_AT.date(),
    )
    return catalyst, link, classification, run


def append_kr_research_input(store: KrThemeStore, source: KrCatalystSource) -> None:
    catalyst = stored_kr_catalyst(source)
    raw_receipt = f'{{"source":"{source.value}"}}'.encode()
    source_run_id = f"{CYCLE_ID}:{source.value}"
    receipt = KrSourceReceipt(
        source_run_id=source_run_id,
        source=source,
        request_key=f"fixture:{source.value}:1",
        received_at=OBSERVED_AT,
        http_status=200 if source is KrCatalystSource.DART else 101,
        content_type="application/json",
        payload_sha256=hashlib.sha256(raw_receipt).hexdigest(),
    )
    observation = KrCatalystObservation(
        collection_cycle_id=CYCLE_ID,
        catalyst_id=catalyst.record.catalyst_id,
        observed_at=OBSERVED_AT,
    )
    with store.writer() as writer:
        stored = writer.append_source_receipt(receipt, raw_receipt).stored
        _ = writer.append_catalyst_from_receipt(
            catalyst.record,
            observation,
            catalyst.raw_payload,
            receipt_id=stored.receipt.receipt_id,
            item_index=0,
        )
        _ = writer.append_source_run(
            KrSourceCollectionRun(
                source_run_id=source_run_id,
                collection_cycle_id=CYCLE_ID,
                source=source,
                adapter_version=_adapter_version(source),
                started_at=OBSERVED_AT,
                completed_at=OBSERVED_AT,
                status=KrCoverageStatus.SUCCESS,
                record_count=1,
                receipt_ids=(stored.receipt.receipt_id,),
                collection_date=OBSERVED_AT.date(),
            )
        )
        _ = writer.append_classification(
            classify_kr_keyword_catalyst(
                catalyst,
                kr_keyword_rules(),
                classification_run_id=CLASSIFICATION_RUN_ID,
                classified_at=CLASSIFIED_AT,
            )
        )


def kr_keyword_rules() -> KrKeywordRuleSet:
    return KrKeywordRuleSet(
        classifier_version="kr-keyword-v1",
        prompt_version="no-prompt-v1",
        rules=(
            KrKeywordRule(
                theme_name="반도체",
                keywords=("semiconductor",),
                related_symbols=(
                    KrRelatedSymbol(
                        symbol="005930",
                        relation=KrThemeRelation.DIRECT_BUSINESS,
                        rationale="registered deterministic rule",
                    ),
                ),
            ),
        ),
    )


def stored_kr_catalyst(source: KrCatalystSource) -> StoredKrCatalyst:
    if source is KrCatalystSource.DART:
        document = {
            "corp_cls": "K",
            "corp_name": "Synthetic Semiconductor Corp",
            "corp_code": "00123456",
            "flr_nm": "Synthetic Semiconductor Corp",
            "rcept_dt": "20260716",
            "rcept_no": "20260716000001",
            "report_nm": "Synthetic semiconductor supply contract",
            "rm": "",
            "stock_code": "123456",
        }
        source_record_id = "opendart://disclosure/20260716000001"
        publisher_id = "00123456"
        published_at = None
    else:
        document = {
            "bodysize": "4200",
            "categoryid": "42",
            "code": "",
            "codeaccu": "",
            "date": "20260716",
            "id": "23",
            "realkey": "202607160901000100000001",
            "time": "090100",
            "title": "Synthetic semiconductor supply contract",
            "tr_cd": "NWS",
            "tr_key": "NWS001",
        }
        source_record_id = "ls-nws://news/202607160901000100000001"
        publisher_id = None
        published_at = dt.datetime(2026, 7, 16, 9, 1, tzinfo=KST)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    record = KrCatalystRecord(
        source=source,
        source_record_id=source_record_id,
        publisher_id=publisher_id,
        published_at=published_at,
        first_observed_at=OBSERVED_AT,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return StoredKrCatalyst(record, payload)


def _adapter_version(source: KrCatalystSource) -> str:
    return OPENDART_ADAPTER_VERSION if source is KrCatalystSource.DART else LS_NWS_ADAPTER_VERSION
