from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

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
from trading_agent.opendart_client import (
    OpenDartDisclosure,
    OpenDartDisclosurePage,
    OpenDartRawResponse,
    OpenDartResponseError,
    OpenDartTransportError,
    parse_opendart_disclosure_page,
)

OPENDART_ADAPTER_VERSION: Final = "opendart-list-v1"
MAX_OPENDART_PAGES: Final = 100


class OpenDartPageFetcher(Protocol):
    def fetch_page(
        self,
        collection_date: dt.date,
        *,
        page_no: int,
    ) -> OpenDartRawResponse: ...


@dataclass(frozen=True, slots=True)
class OpenDartCollectionResult:
    run: KrSourceCollectionRun
    receipt_count: int
    new_receipt_count: int
    catalyst_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool


def collect_opendart_disclosures(
    fetcher: OpenDartPageFetcher,
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    adapter_version: str = OPENDART_ADAPTER_VERSION,
    _parser: Callable[[OpenDartRawResponse], OpenDartDisclosurePage] = (
        parse_opendart_disclosure_page
    ),
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> OpenDartCollectionResult:
    source_run_id = f"{collection_cycle_id}:dart"
    existing = tuple(
        run
        for run in store.source_runs(collection_cycle_id)
        if run.source is KrCatalystSource.DART
    )
    if existing:
        if len(existing) != 1 or existing[0].adapter_version != adapter_version:
            raise ValueError("incompatible OpenDART source run")
        run = existing[0]
        return OpenDartCollectionResult(
            run=run,
            receipt_count=len(run.receipt_ids),
            new_receipt_count=0,
            catalyst_count=run.record_count,
            new_catalyst_count=0,
            new_observation_count=0,
            restarted=True,
        )

    new_receipt_count = 0
    new_catalyst_count = 0
    new_observation_count = 0
    seen_receipt_numbers: set[str] = set()
    expected_metadata: tuple[int, int, int] | None = None
    failure_code: str | None = None
    page_no = 1

    while failure_code is None:
        try:
            raw_response = fetcher.fetch_page(
                collection_date,
                page_no=page_no,
            )
        except OpenDartTransportError:
            failure_code = "transport_error"
            break
        receipt = KrSourceReceipt(
            source_run_id=source_run_id,
            source=KrCatalystSource.DART,
            request_key=raw_response.request_key,
            received_at=raw_response.received_at,
            http_status=raw_response.status_code,
            content_type=raw_response.content_type,
            payload_sha256=hashlib.sha256(raw_response.raw_payload).hexdigest(),
        )
        with store.writer() as writer:
            receipt_result = writer.append_source_receipt(
                receipt,
                raw_response.raw_payload,
            )
        new_receipt_count += int(receipt_result.receipt_inserted)
        stored_receipt = receipt_result.stored
        effective_response = OpenDartRawResponse(
            request_key=stored_receipt.receipt.request_key,
            requested_page=page_no,
            received_at=stored_receipt.receipt.received_at,
            status_code=stored_receipt.receipt.http_status,
            content_type=stored_receipt.receipt.content_type,
            raw_payload=stored_receipt.raw_payload,
        )
        try:
            page = _parser(effective_response)
        except OpenDartResponseError as error:
            failure_code = error.failure_code
            break
        if page.no_data:
            if page_no != 1:
                failure_code = "pagination_changed"
            else:
                expected_metadata = (page.page_count, 0, 0)
            break

        metadata = (page.page_count, page.total_count, page.total_page)
        if expected_metadata is None:
            expected_metadata = metadata
            if page.total_page > MAX_OPENDART_PAGES:
                failure_code = "page_limit_exceeded"
                break
        elif metadata != expected_metadata:
            failure_code = "pagination_changed"
            break

        receipt_numbers = tuple(item.rcept_no for item in page.disclosures)
        if (
            len(receipt_numbers) != len(set(receipt_numbers))
            or any(item in seen_receipt_numbers for item in receipt_numbers)
        ):
            failure_code = "duplicate_disclosure"
            break

        with store.writer() as writer:
            for item_index, disclosure in enumerate(page.disclosures):
                payload = _canonical_disclosure_payload(disclosure)
                record = KrCatalystRecord(
                    source=KrCatalystSource.DART,
                    source_record_id=(
                        f"opendart://disclosure/{disclosure.rcept_no}"
                    ),
                    publisher_id=disclosure.corp_code,
                    published_at=None,
                    first_observed_at=stored_receipt.receipt.received_at,
                    content_type="application/json",
                    payload_sha256=hashlib.sha256(payload).hexdigest(),
                )
                observation = KrCatalystObservation(
                    collection_cycle_id=collection_cycle_id,
                    catalyst_id=record.catalyst_id,
                    observed_at=stored_receipt.receipt.received_at,
                )
                result = writer.append_catalyst_from_receipt(
                    record,
                    observation,
                    payload,
                    receipt_id=stored_receipt.receipt.receipt_id,
                    item_index=item_index,
                )
                new_catalyst_count += int(result.catalyst_inserted)
                new_observation_count += int(result.observation_inserted)
        seen_receipt_numbers.update(receipt_numbers)
        if page_no >= page.total_page:
            break
        page_no += 1

    if (
        failure_code is None
        and expected_metadata is not None
        and len(seen_receipt_numbers) != expected_metadata[1]
    ):
        failure_code = "pagination_count_mismatch"

    receipts = store.source_receipts(source_run_id)
    observed_count = _source_observation_count(
        store,
        collection_cycle_id=collection_cycle_id,
        source=KrCatalystSource.DART,
    )
    if receipts:
        started_at = min(item.receipt.received_at for item in receipts)
        completed_at = max(item.receipt.received_at for item in receipts)
    else:
        started_at = _clock()
        completed_at = started_at
    run = KrSourceCollectionRun(
        source_run_id=source_run_id,
        collection_cycle_id=collection_cycle_id,
        source=KrCatalystSource.DART,
        adapter_version=adapter_version,
        started_at=started_at,
        completed_at=completed_at,
        status=(
            KrCoverageStatus.SUCCESS
            if failure_code is None
            else KrCoverageStatus.FAILED
        ),
        record_count=observed_count,
        failure_code=failure_code,
        receipt_ids=tuple(sorted(item.receipt.receipt_id for item in receipts)),
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)
    return OpenDartCollectionResult(
        run=run,
        receipt_count=len(receipts),
        new_receipt_count=new_receipt_count,
        catalyst_count=observed_count,
        new_catalyst_count=new_catalyst_count,
        new_observation_count=new_observation_count,
        restarted=False,
    )


def _canonical_disclosure_payload(disclosure: OpenDartDisclosure) -> bytes:
    return json.dumps(
        disclosure.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _source_observation_count(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    source: KrCatalystSource,
) -> int:
    sources = {
        item.record.catalyst_id: item.record.source
        for item in store.catalysts()
    }
    return sum(
        observation.collection_cycle_id == collection_cycle_id
        and sources.get(observation.catalyst_id) is source
        for observation in store.observations()
    )
