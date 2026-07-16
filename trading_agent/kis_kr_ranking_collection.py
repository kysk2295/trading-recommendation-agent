from __future__ import annotations

import datetime as dt
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from trading_agent.kis_kr_ranking import (
    MAX_PAGES_PER_KIND,
    KisKrRankingKind,
    KisKrRankingPage,
    KisKrRankingRawResponse,
    KisKrRankingResponseError,
    KisKrRankingTransportError,
    canonical_kis_kr_ranking_item,
    parse_kis_kr_ranking_page,
)
from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_theme_models import (
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import KrThemeStore

KIS_KR_RANKING_ADAPTER_VERSION: Final = "kis-kr-ranking-v1"
TRANSIENT_STATUS_CODES: Final = frozenset({500, 502, 503, 504})
REQUEST_DELAY_SECONDS: Final = 0.08
_KST: Final = ZoneInfo("Asia/Seoul")


class KisKrRankingPageFetcher(Protocol):
    def fetch_page(
        self,
        kind: KisKrRankingKind,
        *,
        page_no: int,
        attempt: int,
        tr_cont: str,
    ) -> KisKrRankingRawResponse: ...


@dataclass(frozen=True, slots=True)
class KisKrRankingCollectionResult:
    run: KrSourceCollectionRun
    receipt_count: int
    new_receipt_count: int
    catalyst_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool


def resume_kis_kr_ranking_collection(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    adapter_version: str = KIS_KR_RANKING_ADAPTER_VERSION,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> KisKrRankingCollectionResult | None:
    if not store.path.is_file():
        return None
    source_run_id = f"{collection_cycle_id}:kis_ranking"
    existing = tuple(
        run
        for run in store.source_runs(collection_cycle_id)
        if run.source is KrCatalystSource.KIS_RANKING
    )
    if existing:
        if (
            len(existing) != 1
            or existing[0].source_run_id != source_run_id
            or existing[0].adapter_version != adapter_version
            or existing[0].collection_date != collection_date
        ):
            raise ValueError("incompatible KIS KR ranking source run")
        run = existing[0]
        return KisKrRankingCollectionResult(
            run=run,
            receipt_count=len(run.receipt_ids),
            new_receipt_count=0,
            catalyst_count=run.record_count,
            new_catalyst_count=0,
            new_observation_count=0,
            restarted=True,
        )

    orphan_receipts = store.source_receipts(source_run_id)
    if not orphan_receipts:
        return None
    run = _terminal_run(
        store,
        source_run_id=source_run_id,
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
        adapter_version=adapter_version,
        failure_code="incomplete_restart",
        clock=_clock,
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)
    return KisKrRankingCollectionResult(
        run=run,
        receipt_count=len(orphan_receipts),
        new_receipt_count=0,
        catalyst_count=run.record_count,
        new_catalyst_count=0,
        new_observation_count=0,
        restarted=True,
    )


def collect_kis_kr_rankings(
    fetcher: KisKrRankingPageFetcher,
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    adapter_version: str = KIS_KR_RANKING_ADAPTER_VERSION,
    _parser: Callable[[KisKrRankingRawResponse], KisKrRankingPage] = (
        parse_kis_kr_ranking_page
    ),
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    _sleeper: Callable[[float], None] = time.sleep,
) -> KisKrRankingCollectionResult:
    source_run_id = f"{collection_cycle_id}:kis_ranking"
    with store.writer():
        pass
    resumed = resume_kis_kr_ranking_collection(
        store,
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
        adapter_version=adapter_version,
        _clock=_clock,
    )
    if resumed is not None:
        return resumed

    new_receipt_count = 0
    new_catalyst_count = 0
    new_observation_count = 0
    request_count = 0
    failure_code: str | None = None

    for kind in KisKrRankingKind:
        seen_symbols: set[str] = set()
        seen_ranks: set[int] = set()
        page_no = 1
        request_tr_cont = ""

        while failure_code is None:
            parsed_page: KisKrRankingPage | None = None
            successful_raw: KisKrRankingRawResponse | None = None
            successful_receipt_id: str | None = None

            for attempt in (1, 2):
                if request_count > 0:
                    _sleeper(REQUEST_DELAY_SECONDS)
                request_count += 1
                try:
                    raw = fetcher.fetch_page(
                        kind,
                        page_no=page_no,
                        attempt=attempt,
                        tr_cont=request_tr_cont,
                    )
                except KisKrRankingTransportError:
                    failure_code = "transport_error"
                    break

                receipt = KrSourceReceipt(
                    source_run_id=source_run_id,
                    source=KrCatalystSource.KIS_RANKING,
                    request_key=raw.request_key,
                    received_at=raw.received_at,
                    http_status=raw.status_code,
                    content_type=raw.content_type,
                    payload_sha256=hashlib.sha256(raw.raw_payload).hexdigest(),
                )
                with store.writer() as writer:
                    receipt_result = writer.append_source_receipt(
                        receipt,
                        raw.raw_payload,
                    )
                new_receipt_count += int(receipt_result.receipt_inserted)
                stored = receipt_result.stored
                effective_raw = KisKrRankingRawResponse(
                    kind=raw.kind,
                    page_no=raw.page_no,
                    attempt=raw.attempt,
                    request_tr_cont=raw.request_tr_cont,
                    response_tr_cont=raw.response_tr_cont,
                    request_key=stored.receipt.request_key,
                    received_at=stored.receipt.received_at,
                    status_code=stored.receipt.http_status,
                    content_type=stored.receipt.content_type,
                    raw_payload=stored.raw_payload,
                )

                if (
                    effective_raw.kind is not kind
                    or effective_raw.page_no != page_no
                    or effective_raw.attempt != attempt
                    or effective_raw.request_tr_cont != request_tr_cont
                ):
                    failure_code = "request_identity_mismatch"
                    break
                if effective_raw.received_at.astimezone(_KST).date() != collection_date:
                    failure_code = "observation_date_mismatch"
                    break
                if effective_raw.response_tr_cont == "INVALID":
                    failure_code = "invalid_continuation"
                    break
                if (
                    effective_raw.status_code in TRANSIENT_STATUS_CODES
                    and attempt == 1
                ):
                    continue
                try:
                    parsed_page = _parser(effective_raw)
                except KisKrRankingResponseError as error:
                    failure_code = error.failure_code
                    break
                successful_raw = effective_raw
                successful_receipt_id = stored.receipt.receipt_id
                break

            if (
                failure_code is not None
                or parsed_page is None
                or successful_raw is None
                or successful_receipt_id is None
            ):
                break

            page_symbols = {item.symbol for item in parsed_page.items}
            page_ranks = {item.rank for item in parsed_page.items}
            if page_symbols & seen_symbols:
                failure_code = "duplicate_symbol"
                break
            if page_ranks & seen_ranks:
                failure_code = "duplicate_rank"
                break

            with store.writer() as writer:
                for item_index, item in enumerate(parsed_page.items):
                    payload = canonical_kis_kr_ranking_item(item)
                    record = KrCatalystRecord(
                        source=KrCatalystSource.KIS_RANKING,
                        source_record_id=(
                            f"kis-ranking://{collection_cycle_id}/"
                            f"{kind.value}/{item.symbol}"
                        ),
                        publisher_id="kis_domestic_market_data",
                        published_at=None,
                        first_observed_at=successful_raw.received_at,
                        content_type="application/json",
                        payload_sha256=hashlib.sha256(payload).hexdigest(),
                    )
                    observation = KrCatalystObservation(
                        collection_cycle_id=collection_cycle_id,
                        catalyst_id=record.catalyst_id,
                        observed_at=successful_raw.received_at,
                    )
                    append_result = writer.append_catalyst_from_receipt(
                        record,
                        observation,
                        payload,
                        receipt_id=successful_receipt_id,
                        item_index=item_index,
                    )
                    new_catalyst_count += int(append_result.catalyst_inserted)
                    new_observation_count += int(append_result.observation_inserted)

            seen_symbols.update(page_symbols)
            seen_ranks.update(page_ranks)
            if successful_raw.response_tr_cont == "M":
                if page_no >= MAX_PAGES_PER_KIND:
                    failure_code = "page_limit_exceeded"
                    break
                page_no += 1
                request_tr_cont = "N"
                continue
            break

        if failure_code is not None:
            break

    run = _terminal_run(
        store,
        source_run_id=source_run_id,
        collection_cycle_id=collection_cycle_id,
        collection_date=collection_date,
        adapter_version=adapter_version,
        failure_code=failure_code,
        clock=_clock,
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)
    receipts = store.source_receipts(source_run_id)
    return KisKrRankingCollectionResult(
        run=run,
        receipt_count=len(receipts),
        new_receipt_count=new_receipt_count,
        catalyst_count=run.record_count,
        new_catalyst_count=new_catalyst_count,
        new_observation_count=new_observation_count,
        restarted=False,
    )


def _terminal_run(
    store: KrThemeStore,
    *,
    source_run_id: str,
    collection_cycle_id: str,
    collection_date: dt.date,
    adapter_version: str,
    failure_code: str | None,
    clock: Callable[[], dt.datetime],
) -> KrSourceCollectionRun:
    receipts = store.source_receipts(source_run_id)
    if receipts:
        started_at = min(item.receipt.received_at for item in receipts)
        completed_at = max(item.receipt.received_at for item in receipts)
    else:
        started_at = clock()
        completed_at = started_at
    return KrSourceCollectionRun(
        source_run_id=source_run_id,
        collection_cycle_id=collection_cycle_id,
        source=KrCatalystSource.KIS_RANKING,
        adapter_version=adapter_version,
        started_at=started_at,
        completed_at=completed_at,
        status=(
            KrCoverageStatus.SUCCESS
            if failure_code is None
            else KrCoverageStatus.FAILED
        ),
        record_count=_source_observation_count(
            store,
            collection_cycle_id=collection_cycle_id,
        ),
        failure_code=failure_code,
        receipt_ids=tuple(sorted(item.receipt.receipt_id for item in receipts)),
        collection_date=collection_date,
    )


def _source_observation_count(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
) -> int:
    sources = {
        item.record.catalyst_id: item.record.source for item in store.catalysts()
    }
    return sum(
        observation.collection_cycle_id == collection_cycle_id
        and sources.get(observation.catalyst_id) is KrCatalystSource.KIS_RANKING
        for observation in store.observations()
    )
