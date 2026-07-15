from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol, override
from zoneinfo import ZoneInfo

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
from trading_agent.ls_nws import (
    LsNwsParseError,
    LsNwsRawFrame,
    ParsedLsNwsNews,
    parse_ls_nws_frame,
)
from trading_agent.ls_nws_stream import LsNwsStreamUnavailableError
from trading_agent.ls_token import LsTokenResponseError, LsTokenTransportError

LS_NWS_ADAPTER_VERSION: Final = "ls-nws-v1"
MAX_LS_NWS_COLLECTION_SECONDS: Final = 86_400.0
MAX_LS_NWS_COLLECTION_FRAMES: Final = 100_000
_SAFE_CYCLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,121}$")
_KST: Final = ZoneInfo("Asia/Seoul")


class LsNwsCollectionInputError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS NWS collection 입력 범위가 유효하지 않습니다"


class LsNwsFrameReceiver(Protocol):
    def receive_frame(self, timeout_seconds: float) -> LsNwsRawFrame | None: ...


type LsNwsReceiverOpener = Callable[
    [],
    AbstractContextManager[LsNwsFrameReceiver],
]
type LsNwsParser = Callable[
    [LsNwsRawFrame, dt.date],
    ParsedLsNwsNews,
]


@dataclass(frozen=True, slots=True)
class LsNwsCollectionResult:
    run: KrSourceCollectionRun
    receipt_count: int
    new_receipt_count: int
    catalyst_count: int
    new_catalyst_count: int
    new_observation_count: int
    restarted: bool


def collect_ls_nws_news(
    opener: LsNwsReceiverOpener,
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    duration_seconds: float,
    max_frames: int,
    adapter_version: str = LS_NWS_ADAPTER_VERSION,
    _parser: LsNwsParser = lambda frame, date: parse_ls_nws_frame(
        frame,
        collection_date=date,
    ),
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    _monotonic: Callable[[], float] = time.monotonic,
) -> LsNwsCollectionResult:
    if (
        _SAFE_CYCLE_ID.fullmatch(collection_cycle_id) is None
        or isinstance(collection_date, dt.datetime)
        or not isinstance(collection_date, dt.date)
        or not math.isfinite(duration_seconds)
        or not 0 < duration_seconds <= MAX_LS_NWS_COLLECTION_SECONDS
        or isinstance(max_frames, bool)
        or not 1 <= max_frames <= MAX_LS_NWS_COLLECTION_FRAMES
    ):
        raise LsNwsCollectionInputError
    source_run_id = f"{collection_cycle_id}:news"
    with store.writer():
        pass
    existing = tuple(
        run
        for run in store.source_runs(collection_cycle_id)
        if run.source is KrCatalystSource.NEWS
    )
    if existing:
        if (
            len(existing) != 1
            or existing[0].source_run_id != source_run_id
            or existing[0].adapter_version != adapter_version
        ):
            raise ValueError("incompatible LS NWS source run")
        run = existing[0]
        if run.collection_date != collection_date:
            raise LsNwsCollectionInputError
        return LsNwsCollectionResult(
            run=run,
            receipt_count=len(run.receipt_ids),
            new_receipt_count=0,
            catalyst_count=run.record_count,
            new_catalyst_count=0,
            new_observation_count=0,
            restarted=True,
        )

    orphan_receipts = store.source_receipts(source_run_id)
    if orphan_receipts:
        if any(
            item.receipt.received_at.astimezone(_KST).date() != collection_date
            for item in orphan_receipts
        ):
            raise LsNwsCollectionInputError
        observed_count = _source_observation_count(
            store,
            collection_cycle_id=collection_cycle_id,
        )
        receipt_times = tuple(
            item.receipt.received_at for item in orphan_receipts
        )
        started_at = min(receipt_times)
        completed_at = max((started_at, _clock(), *receipt_times))
        run = KrSourceCollectionRun(
            source_run_id=source_run_id,
            collection_cycle_id=collection_cycle_id,
            source=KrCatalystSource.NEWS,
            adapter_version=adapter_version,
            started_at=started_at,
            completed_at=completed_at,
            status=KrCoverageStatus.FAILED,
            record_count=observed_count,
            failure_code="interrupted_run",
            receipt_ids=tuple(
                sorted(item.receipt.receipt_id for item in orphan_receipts)
            ),
            collection_date=collection_date,
        )
        with store.writer() as writer:
            _ = writer.append_source_run(run)
        return LsNwsCollectionResult(
            run=run,
            receipt_count=len(orphan_receipts),
            new_receipt_count=0,
            catalyst_count=observed_count,
            new_catalyst_count=0,
            new_observation_count=0,
            restarted=True,
        )

    operation_started_at = _clock()
    started_monotonic = _monotonic()
    new_receipt_count = 0
    new_catalyst_count = 0
    new_observation_count = 0
    seen_realkeys: set[str] = set()
    failure_code: str | None = None
    expected_sequence = 1

    try:
        with opener() as receiver:
            while failure_code is None and expected_sequence <= max_frames:
                elapsed = _monotonic() - started_monotonic
                remaining = duration_seconds - elapsed
                if remaining <= 0:
                    break
                frame = receiver.receive_frame(remaining)
                if frame is None:
                    break
                receipt = KrSourceReceipt(
                    source_run_id=source_run_id,
                    source=KrCatalystSource.NEWS,
                    request_key=(
                        f"ls:nws:frame:{frame.sequence:06d}:{frame.wire_kind.value}"
                    ),
                    received_at=frame.received_at,
                    http_status=101,
                    content_type="application/json",
                    payload_sha256=hashlib.sha256(frame.raw_payload).hexdigest(),
                )
                with store.writer() as writer:
                    receipt_result = writer.append_source_receipt(
                        receipt,
                        frame.raw_payload,
                    )
                new_receipt_count += int(receipt_result.receipt_inserted)
                stored_receipt = receipt_result.stored
                if frame.sequence != expected_sequence:
                    failure_code = "frame_sequence"
                    break
                effective_frame = LsNwsRawFrame(
                    sequence=frame.sequence,
                    received_at=stored_receipt.receipt.received_at,
                    wire_kind=frame.wire_kind,
                    raw_payload=stored_receipt.raw_payload,
                )
                try:
                    parsed = _parser(effective_frame, collection_date)
                except LsNwsParseError as error:
                    failure_code = error.failure_code
                    break
                if parsed.realkey in seen_realkeys:
                    failure_code = "duplicate_news"
                    break
                payload = parsed.canonical_payload
                record = KrCatalystRecord(
                    source=KrCatalystSource.NEWS,
                    source_record_id=parsed.source_record_id,
                    publisher_id=None,
                    published_at=parsed.published_at,
                    first_observed_at=stored_receipt.receipt.received_at,
                    content_type="application/json",
                    payload_sha256=hashlib.sha256(payload).hexdigest(),
                )
                observation = KrCatalystObservation(
                    collection_cycle_id=collection_cycle_id,
                    catalyst_id=record.catalyst_id,
                    observed_at=stored_receipt.receipt.received_at,
                )
                with store.writer() as writer:
                    result = writer.append_catalyst_from_receipt(
                        record,
                        observation,
                        payload,
                        receipt_id=stored_receipt.receipt.receipt_id,
                        item_index=0,
                    )
                new_catalyst_count += int(result.catalyst_inserted)
                new_observation_count += int(result.observation_inserted)
                seen_realkeys.add(parsed.realkey)
                expected_sequence += 1
    except (LsTokenResponseError, LsTokenTransportError):
        failure_code = "token_error"
    except LsNwsStreamUnavailableError:
        failure_code = "stream_unavailable"

    receipts = store.source_receipts(source_run_id)
    observed_count = _source_observation_count(
        store,
        collection_cycle_id=collection_cycle_id,
    )
    completed_clock = _clock()
    receipt_times = tuple(item.receipt.received_at for item in receipts)
    started_at = min((operation_started_at, *receipt_times))
    completed_at = max((started_at, completed_clock, *receipt_times))
    run = KrSourceCollectionRun(
        source_run_id=source_run_id,
        collection_cycle_id=collection_cycle_id,
        source=KrCatalystSource.NEWS,
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
        collection_date=collection_date,
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)
    return LsNwsCollectionResult(
        run=run,
        receipt_count=len(receipts),
        new_receipt_count=new_receipt_count,
        catalyst_count=observed_count,
        new_catalyst_count=new_catalyst_count,
        new_observation_count=new_observation_count,
        restarted=False,
    )


def _source_observation_count(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
) -> int:
    sources = {
        item.record.catalyst_id: item.record.source
        for item in store.catalysts()
    }
    return sum(
        observation.collection_cycle_id == collection_cycle_id
        and sources.get(observation.catalyst_id) is KrCatalystSource.NEWS
        for observation in store.observations()
    )
