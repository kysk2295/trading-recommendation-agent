from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import assert_never, override

from pydantic import ValidationError

from trading_agent.data_capability_models import DataSourceId
from trading_agent.kr_source_collection_models import (
    KrCatalystObservationReceipt,
    KrSourceCollectionRun,
)
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrClassifierKind,
    KrCoverageStatus,
    KrThemeClassification,
    KrThemeDirection,
)
from trading_agent.kr_theme_store import StoredKrCatalyst
from trading_agent.ls_nws import (
    LsNwsParseError,
    LsNwsRawFrame,
    LsNwsWireKind,
    parse_ls_nws_frame,
)
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_client import OpenDartDisclosure
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION
from trading_agent.research_evidence_models import JsonValue

_LS_BASE_KEYS = frozenset({"bodysize", "code", "date", "id", "realkey", "time", "title", "tr_cd", "tr_key"})
_LS_EXTENSION_KEYS = frozenset({"categoryid", "codeaccu"})


class KrNormalizedCatalystValidationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR normalized catalyst validation is blocked"


@dataclass(frozen=True, slots=True)
class ValidatedKrNormalizedCatalyst:
    source_id: DataSourceId


def validate_kr_keyword_research_input(
    catalyst: StoredKrCatalyst,
    link: KrCatalystObservationReceipt,
    classification: KrThemeClassification,
    run: KrSourceCollectionRun,
) -> ValidatedKrNormalizedCatalyst:
    try:
        _validate_lineage(catalyst, link, classification, run)
        source_id = _validate_payload(catalyst, run)
        return ValidatedKrNormalizedCatalyst(source_id)
    except KrNormalizedCatalystValidationError:
        raise
    except (LsNwsParseError, TypeError, UnicodeError, ValidationError, ValueError):
        raise KrNormalizedCatalystValidationError from None


def _validate_lineage(
    catalyst: StoredKrCatalyst,
    link: KrCatalystObservationReceipt,
    classification: KrThemeClassification,
    run: KrSourceCollectionRun,
) -> None:
    if (
        type(catalyst) is not StoredKrCatalyst
        or type(link) is not KrCatalystObservationReceipt
        or type(classification) is not KrThemeClassification
        or type(run) is not KrSourceCollectionRun
        or catalyst.record.source not in (KrCatalystSource.DART, KrCatalystSource.NEWS)
        or run.source is not catalyst.record.source
        or run.collection_cycle_id != link.collection_cycle_id
        or run.status is not KrCoverageStatus.SUCCESS
        or run.record_count <= 0
        or type(run.collection_date) is not dt.date
        or not run.started_at <= catalyst.record.first_observed_at <= run.completed_at
        or link.catalyst_id != catalyst.record.catalyst_id
        or link.item_payload_sha256 != catalyst.record.payload_sha256
        or link.receipt_id not in run.receipt_ids
        or classification.catalyst_id != catalyst.record.catalyst_id
        or classification.classifier_kind is not KrClassifierKind.KEYWORD
        or classification.classified_at < catalyst.record.first_observed_at
        or classification.direction is not KrThemeDirection.POSITIVE
        or classification.theme_name is None
        or not classification.related_symbols
        or catalyst.record.content_type != "application/json"
        or hashlib.sha256(catalyst.raw_payload).hexdigest() != catalyst.record.payload_sha256
    ):
        raise KrNormalizedCatalystValidationError


def _validate_payload(
    catalyst: StoredKrCatalyst,
    run: KrSourceCollectionRun,
) -> DataSourceId:
    match catalyst.record.source:
        case KrCatalystSource.DART:
            return _validate_dart(catalyst, run)
        case KrCatalystSource.NEWS:
            return _validate_news(catalyst, run)
        case KrCatalystSource.KIS_RANKING | KrCatalystSource.VOLUME_SURGE:
            raise KrNormalizedCatalystValidationError
        case unreachable:
            assert_never(unreachable)


def _validate_dart(
    catalyst: StoredKrCatalyst,
    run: KrSourceCollectionRun,
) -> DataSourceId:
    collection_date = _collection_date(run.collection_date)
    disclosure = OpenDartDisclosure.model_validate_json(catalyst.raw_payload)
    canonical = json.dumps(
        disclosure.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if (
        run.adapter_version != OPENDART_ADAPTER_VERSION
        or disclosure.rcept_dt != collection_date.strftime("%Y%m%d")
        or canonical != catalyst.raw_payload
        or catalyst.record.source_record_id != f"opendart://disclosure/{disclosure.rcept_no}"
        or catalyst.record.publisher_id != disclosure.corp_code
        or catalyst.record.published_at is not None
    ):
        raise KrNormalizedCatalystValidationError
    return DataSourceId(provider="opendart", feed="list")


def _validate_news(
    catalyst: StoredKrCatalyst,
    run: KrSourceCollectionRun,
) -> DataSourceId:
    collection_date = _collection_date(run.collection_date)
    document = json.loads(catalyst.raw_payload, object_pairs_hook=_unique_json_object)
    if not isinstance(document, dict):
        raise KrNormalizedCatalystValidationError
    keys = frozenset(document)
    if keys not in (_LS_BASE_KEYS, _LS_BASE_KEYS | _LS_EXTENSION_KEYS):
        raise KrNormalizedCatalystValidationError
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    body = {key: value for key, value in document.items() if key not in {"tr_cd", "tr_key"}}
    wire_payload = json.dumps(
        {
            "body": body,
            "header": {"tr_cd": document["tr_cd"], "tr_key": document["tr_key"]},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    parsed = parse_ls_nws_frame(
        LsNwsRawFrame(
            sequence=1,
            received_at=catalyst.record.first_observed_at,
            wire_kind=LsNwsWireKind.TEXT,
            raw_payload=wire_payload,
        ),
        collection_date=collection_date,
    )
    if (
        run.adapter_version != LS_NWS_ADAPTER_VERSION
        or canonical != catalyst.raw_payload
        or parsed.canonical_payload != catalyst.raw_payload
        or parsed.source_record_id != catalyst.record.source_record_id
        or parsed.published_at != catalyst.record.published_at
        or catalyst.record.publisher_id is not None
    ):
        raise KrNormalizedCatalystValidationError
    return DataSourceId(provider="ls", feed="nws")


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise KrNormalizedCatalystValidationError
        result[key] = value
    return result


def _collection_date(value: dt.date | None) -> dt.date:
    if type(value) is not dt.date:
        raise KrNormalizedCatalystValidationError
    return value


__all__ = (
    "KrNormalizedCatalystValidationError",
    "ValidatedKrNormalizedCatalyst",
    "validate_kr_keyword_research_input",
)
