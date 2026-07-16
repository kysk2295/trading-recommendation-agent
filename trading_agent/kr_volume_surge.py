from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Final, override

from pydantic import ValidationError

from trading_agent.kis_kr_ranking import KisKrRankingItem, KisKrRankingKind
from trading_agent.kis_kr_ranking_collection import (
    KIS_KR_RANKING_ADAPTER_VERSION,
)
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import (
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import KrThemeStore, StoredKrCatalyst
from trading_agent.kr_volume_surge_models import (
    InvalidKrVolumeSurgePayloadError,
    KrVolumeSurgePayloadV2,
    KrVolumeSurgeSymbolV2,
    canonical_kr_volume_surge_payload,
    parse_kr_volume_surge_payload,
)

KR_VOLUME_SURGE_ADAPTER_VERSION: Final = "kis-ranking-volume-surge-v2"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,115}$")
_RATIO_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


class KrVolumeSurgeSourceNotReadyError(ValueError):
    @override
    def __str__(self) -> str:
        return "volume surge upstream KIS source가 아직 terminal이 아닙니다"


class InvalidKrVolumeSurgeSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR volume surge source 계보가 유효하지 않습니다"


@dataclass(frozen=True, slots=True)
class KrVolumeSurgeDerivationResult:
    run: KrSourceCollectionRun
    symbol_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool


@dataclass(frozen=True, slots=True)
class _VolumeRowEvidence:
    stored: StoredKrCatalyst
    item: KisKrRankingItem


@dataclass(frozen=True, slots=True)
class _UpstreamEvidence:
    run: KrSourceCollectionRun
    source_observed_at: dt.datetime
    rows: tuple[_VolumeRowEvidence, ...]


class _DerivationFailure(ValueError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code


def derive_kr_volume_surge(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    _after_catalyst: Callable[[], None] = lambda: None,
) -> KrVolumeSurgeDerivationResult:
    _validate_request(collection_cycle_id, collection_date)
    source_run_id = f"{collection_cycle_id}:volume_surge"
    resumed = _terminal_result(
        store,
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
        source_run_id=source_run_id,
    )
    if resumed is not None:
        return resumed

    upstream = _upstream_run(
        store,
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
    )
    if upstream.status is KrCoverageStatus.FAILED:
        derived_at = _aware_clock(_clock)
        return _append_failed_run(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=collection_date,
            derived_at=derived_at,
            failure_code="upstream_kis_failed",
        )

    orphan = _orphan_catalyst(
        store,
        collection_cycle_id=collection_cycle_id,
        source_run_id=f"{collection_cycle_id}:kis_ranking",
    )
    derived_at: dt.datetime | None = None
    try:
        evidence = _upstream_evidence(
            store,
            upstream,
            collection_cycle_id=collection_cycle_id,
        )
        if orphan is not None:
            payload = _payload(
                evidence,
                source_run_id=f"{collection_cycle_id}:kis_ranking",
                derived_at=orphan.record.first_observed_at,
            )
            raw_payload = canonical_kr_volume_surge_payload(payload)
            _validate_orphan(
                orphan,
                raw_payload,
                payload,
                collection_cycle_id=collection_cycle_id,
            )
            run = _terminal_run(
                collection_cycle_id=collection_cycle_id,
                collection_date=collection_date,
                derived_at=payload.observed_at,
                status=KrCoverageStatus.SUCCESS,
                record_count=1,
                failure_code=None,
            )
            with store.writer() as writer:
                _ = writer.append_source_run(run)
            return KrVolumeSurgeDerivationResult(
                run=run,
                symbol_count=len(payload.symbols),
                new_catalyst_count=0,
                new_observation_count=0,
                restarted=True,
            )

        derived_at = _aware_clock(_clock)
        if derived_at < max(upstream.completed_at, evidence.source_observed_at):
            raise _DerivationFailure("invalid_derivation_time")
        payload = _payload(
            evidence,
            source_run_id=f"{collection_cycle_id}:kis_ranking",
            derived_at=derived_at,
        )
    except _DerivationFailure as error:
        if derived_at is None:
            derived_at = _aware_clock(_clock)
        return _append_failed_run(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=collection_date,
            derived_at=derived_at,
            failure_code=error.failure_code,
        )

    raw_payload = canonical_kr_volume_surge_payload(payload)
    record = _derived_record(
        collection_cycle_id=collection_cycle_id,
        observed_at=payload.observed_at,
        raw_payload=raw_payload,
    )
    observation = KrCatalystObservation(
        collection_cycle_id=collection_cycle_id,
        catalyst_id=record.catalyst_id,
        observed_at=payload.observed_at,
    )
    with store.writer() as writer:
        append_result = writer.append_catalyst(
            record,
            observation,
            raw_payload,
        )
    _after_catalyst()
    run = _terminal_run(
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
        derived_at=payload.observed_at,
        status=KrCoverageStatus.SUCCESS,
        record_count=1,
        failure_code=None,
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)
    return KrVolumeSurgeDerivationResult(
        run=run,
        symbol_count=len(payload.symbols),
        new_catalyst_count=int(append_result.catalyst_inserted),
        new_observation_count=int(append_result.observation_inserted),
        restarted=False,
    )


def _validate_request(collection_cycle_id: str, collection_date: dt.date) -> None:
    if (
        _SAFE_ID.fullmatch(collection_cycle_id) is None
        or isinstance(collection_date, dt.datetime)
    ):
        raise InvalidKrVolumeSurgeSourceError


def _terminal_result(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    source_run_id: str,
) -> KrVolumeSurgeDerivationResult | None:
    existing = tuple(
        run
        for run in store.source_runs(collection_cycle_id)
        if run.source is KrCatalystSource.VOLUME_SURGE
    )
    if not existing:
        return None
    if (
        len(existing) != 1
        or existing[0].source_run_id != source_run_id
        or existing[0].adapter_version != KR_VOLUME_SURGE_ADAPTER_VERSION
        or existing[0].collection_date != collection_date
    ):
        raise InvalidKrVolumeSurgeSourceError
    run = existing[0]
    if run.status is KrCoverageStatus.FAILED:
        if run.record_count != 0:
            raise InvalidKrVolumeSurgeSourceError
        symbol_count = 0
    else:
        orphan = _orphan_catalyst(
            store,
            collection_cycle_id=collection_cycle_id,
            source_run_id=f"{collection_cycle_id}:kis_ranking",
        )
        if orphan is None or run.record_count != 1:
            raise InvalidKrVolumeSurgeSourceError
        try:
            payload = parse_kr_volume_surge_payload(orphan.raw_payload)
        except InvalidKrVolumeSurgePayloadError:
            raise InvalidKrVolumeSurgeSourceError from None
        if not isinstance(payload, KrVolumeSurgePayloadV2):
            raise InvalidKrVolumeSurgeSourceError
        symbol_count = len(payload.symbols)
    return KrVolumeSurgeDerivationResult(
        run=run,
        symbol_count=symbol_count,
        new_catalyst_count=0,
        new_observation_count=0,
        restarted=True,
    )


def _upstream_run(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
) -> KrSourceCollectionRun:
    existing = tuple(
        run
        for run in store.source_runs(collection_cycle_id)
        if run.source is KrCatalystSource.KIS_RANKING
    )
    expected_id = f"{collection_cycle_id}:kis_ranking"
    if (
        len(existing) != 1
        or existing[0].source_run_id != expected_id
        or existing[0].adapter_version != KIS_KR_RANKING_ADAPTER_VERSION
        or existing[0].collection_date != collection_date
    ):
        raise KrVolumeSurgeSourceNotReadyError
    return existing[0]


def _upstream_evidence(
    store: KrThemeStore,
    run: KrSourceCollectionRun,
    *,
    collection_cycle_id: str,
) -> _UpstreamEvidence:
    receipts = store.source_receipts(run.source_run_id)
    receipt_by_id = {item.receipt.receipt_id: item for item in receipts}
    volume_receipts = tuple(
        item
        for item in receipts
        if item.receipt.request_key.startswith("kis-kr:volume:")
    )
    catalysts = {item.record.catalyst_id: item for item in store.catalysts()}
    observations = tuple(
        item
        for item in store.observations()
        if item.collection_cycle_id == collection_cycle_id
        and catalysts.get(item.catalyst_id) is not None
        and catalysts[item.catalyst_id].record.source is KrCatalystSource.KIS_RANKING
    )
    links = {
        item.catalyst_id: item
        for item in store.observation_receipts(collection_cycle_id)
        if item.catalyst_id in {observation.catalyst_id for observation in observations}
    }
    if (
        tuple(sorted(receipt_by_id)) != run.receipt_ids
        or len(receipt_by_id) != len(receipts)
        or not volume_receipts
        or len(observations) != run.record_count
        or len(links) != len(observations)
    ):
        raise _DerivationFailure("invalid_upstream_evidence")

    rows: list[_VolumeRowEvidence] = []
    seen_symbols: set[str] = set()
    for observation in observations:
        stored = catalysts[observation.catalyst_id]
        link = links.get(observation.catalyst_id)
        receipt = None if link is None else receipt_by_id.get(link.receipt_id)
        if (
            link is None
            or receipt is None
            or stored.record.content_type != "application/json"
            or stored.record.first_observed_at != observation.observed_at
            or link.item_payload_sha256 != stored.record.payload_sha256
            or receipt.receipt.received_at > observation.observed_at
        ):
            raise _DerivationFailure("invalid_upstream_evidence")
        try:
            item = KisKrRankingItem.model_validate_json(stored.raw_payload)
        except ValidationError:
            raise _DerivationFailure("invalid_upstream_evidence") from None
        expected_source_record_id = (
            f"kis-ranking://{collection_cycle_id}/"
            f"{item.ranking_kind.value}/{item.symbol}"
        )
        if stored.record.source_record_id != expected_source_record_id:
            raise _DerivationFailure("invalid_upstream_evidence")
        if item.ranking_kind is not KisKrRankingKind.VOLUME:
            continue
        if item.symbol in seen_symbols:
            raise _DerivationFailure("invalid_upstream_evidence")
        seen_symbols.add(item.symbol)
        rows.append(_VolumeRowEvidence(stored=stored, item=item))

    return _UpstreamEvidence(
        run=run,
        source_observed_at=max(
            item.receipt.received_at for item in volume_receipts
        ),
        rows=tuple(sorted(rows, key=lambda item: item.item.symbol)),
    )


def _payload(
    evidence: _UpstreamEvidence,
    *,
    source_run_id: str,
    derived_at: dt.datetime,
) -> KrVolumeSurgePayloadV2:
    symbols: list[KrVolumeSurgeSymbolV2] = []
    for row in evidence.rows:
        item = row.item
        if item.average_volume is None or item.average_volume == 0:
            raise _DerivationFailure("zero_average_volume")
        if item.accumulated_trading_value_krw is None:
            raise _DerivationFailure("invalid_upstream_evidence")
        with localcontext(_RATIO_CONTEXT):
            ratio = Decimal(item.accumulated_volume) / Decimal(item.average_volume)
        symbols.append(
            KrVolumeSurgeSymbolV2(
                symbol=item.symbol,
                trading_value_krw=item.accumulated_trading_value_krw,
                volume_ratio=ratio,
                source_catalyst_id=row.stored.record.catalyst_id,
            )
        )
    return KrVolumeSurgePayloadV2(
        observed_at=derived_at,
        source_observed_at=evidence.source_observed_at,
        source_run_id=source_run_id,
        symbols=tuple(symbols),
    )


def _orphan_catalyst(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    source_run_id: str,
) -> StoredKrCatalyst | None:
    source_record_id = f"volume-surge://{collection_cycle_id}/schema-v2"
    candidates = tuple(
        item
        for item in store.catalysts()
        if item.record.source is KrCatalystSource.VOLUME_SURGE
        and item.record.source_record_id == source_record_id
    )
    volume_observations = tuple(
        item
        for item in store.observations()
        if item.collection_cycle_id == collection_cycle_id
        and any(candidate.record.catalyst_id == item.catalyst_id for candidate in candidates)
    )
    if not candidates and not volume_observations:
        return None
    if len(candidates) != 1 or len(volume_observations) != 1:
        raise InvalidKrVolumeSurgeSourceError
    candidate = candidates[0]
    observation = volume_observations[0]
    try:
        payload = parse_kr_volume_surge_payload(candidate.raw_payload)
    except InvalidKrVolumeSurgePayloadError:
        raise InvalidKrVolumeSurgeSourceError from None
    if (
        not isinstance(payload, KrVolumeSurgePayloadV2)
        or payload.source_run_id != source_run_id
        or candidate.record.first_observed_at != observation.observed_at
        or payload.observed_at != observation.observed_at
    ):
        raise InvalidKrVolumeSurgeSourceError
    return candidate


def _validate_orphan(
    orphan: StoredKrCatalyst,
    raw_payload: bytes,
    payload: KrVolumeSurgePayloadV2,
    *,
    collection_cycle_id: str,
) -> None:
    expected = _derived_record(
        collection_cycle_id=collection_cycle_id,
        observed_at=payload.observed_at,
        raw_payload=raw_payload,
    )
    if orphan.record != expected or orphan.raw_payload != raw_payload:
        raise InvalidKrVolumeSurgeSourceError


def _derived_record(
    *,
    collection_cycle_id: str,
    observed_at: dt.datetime,
    raw_payload: bytes,
) -> KrCatalystRecord:
    return KrCatalystRecord(
        source=KrCatalystSource.VOLUME_SURGE,
        source_record_id=f"volume-surge://{collection_cycle_id}/schema-v2",
        publisher_id="derived_kis_ranking",
        published_at=None,
        first_observed_at=observed_at,
        content_type="application/json",
        payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
    )


def _append_failed_run(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    derived_at: dt.datetime,
    failure_code: str,
) -> KrVolumeSurgeDerivationResult:
    run = _terminal_run(
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
        derived_at=derived_at,
        status=KrCoverageStatus.FAILED,
        record_count=0,
        failure_code=failure_code,
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)
    return KrVolumeSurgeDerivationResult(
        run=run,
        symbol_count=0,
        new_catalyst_count=0,
        new_observation_count=0,
        restarted=False,
    )


def _terminal_run(
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    derived_at: dt.datetime,
    status: KrCoverageStatus,
    record_count: int,
    failure_code: str | None,
) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id=f"{collection_cycle_id}:volume_surge",
        collection_cycle_id=collection_cycle_id,
        source=KrCatalystSource.VOLUME_SURGE,
        adapter_version=KR_VOLUME_SURGE_ADAPTER_VERSION,
        started_at=derived_at,
        completed_at=derived_at,
        status=status,
        record_count=record_count,
        failure_code=failure_code,
        receipt_ids=(),
        collection_date=collection_date,
    )


def _aware_clock(clock: Callable[[], dt.datetime]) -> dt.datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidKrVolumeSurgeSourceError
    return value
