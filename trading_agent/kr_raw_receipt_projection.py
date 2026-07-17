from __future__ import annotations

import datetime as dt
from typing import Final, override

from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
    StoredKrSourceReceipt,
)
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import (
    KrSourceReceiptProjectionSnapshot,
    KrThemeReader,
)
from trading_agent.raw_object_manifest_models import (
    RawObjectPartitionManifest,
    RawReceipt,
    RawReceiptPayload,
)
from trading_agent.raw_receipt_projection import project_raw_receipt_partition

_SOURCE_IDS: Final[dict[KrCatalystSource, str]] = {
    KrCatalystSource.DART: "kr.opendart",
    KrCatalystSource.NEWS: "kr.ls.nws",
    KrCatalystSource.KIS_RANKING: "kr.kis.ranking",
    KrCatalystSource.VOLUME_SURGE: "kr.kis.volume_surge",
}


class InvalidKrRawReceiptProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR raw receipt projection is invalid"


def project_kr_source_run_receipts(
    reader: KrThemeReader,
    *,
    collection_cycle_id: str,
    source: KrCatalystSource,
) -> RawObjectPartitionManifest | None:
    try:
        if (
            not isinstance(reader, KrThemeReader)
            or type(collection_cycle_id) is not str
            or not collection_cycle_id
            or type(source) is not KrCatalystSource
        ):
            raise InvalidKrRawReceiptProjectionError

        snapshot = reader.source_receipt_projection_snapshot(
            collection_cycle_id=collection_cycle_id,
            source=source,
        )
        if (
            type(snapshot) is not KrSourceReceiptProjectionSnapshot
            or type(snapshot.run) is not KrSourceCollectionRun
            or type(snapshot.receipts) is not tuple
            or type(snapshot.parent_ledger_generation) is not int
            or snapshot.parent_ledger_generation < 0
            or snapshot.run.collection_cycle_id != collection_cycle_id
            or snapshot.run.source is not source
        ):
            raise InvalidKrRawReceiptProjectionError
        run = snapshot.run
        if (
            run.status is not KrCoverageStatus.SUCCESS
            or type(run.collection_date) is not dt.date
            or type(run.receipt_ids) is not tuple
            or any(type(receipt_id) is not str for receipt_id in run.receipt_ids)
            or run.receipt_ids != tuple(sorted(set(run.receipt_ids)))
        ):
            raise InvalidKrRawReceiptProjectionError
        canonical_stored = _canonical_selected_receipts(snapshot.receipts, run)
        if not run.receipt_ids:
            if (
                source is not KrCatalystSource.VOLUME_SURGE
                or canonical_stored
                or snapshot.parent_ledger_generation != 0
            ):
                raise InvalidKrRawReceiptProjectionError
            return None

        canonical_ids = tuple(item.receipt.receipt_id for item in canonical_stored)
        if canonical_ids != run.receipt_ids:
            raise InvalidKrRawReceiptProjectionError

        raw_receipts = tuple(
            RawReceipt.from_payload(
                receipt_id=stored.receipt.receipt_id,
                source_id=_SOURCE_IDS[source],
                market_date=run.collection_date,
                received_at=stored.receipt.received_at,
                payload_sha256=stored.receipt.payload_sha256,
                payload=RawReceiptPayload(stored.raw_payload),
            )
            for stored in canonical_stored
        )
        return project_raw_receipt_partition(
            raw_receipts,
            source_id=_SOURCE_IDS[source],
            market_date=run.collection_date,
            parent_ledger_generation=snapshot.parent_ledger_generation,
        )
    except InvalidKrRawReceiptProjectionError:
        raise
    except Exception:
        raise InvalidKrRawReceiptProjectionError from None


def _canonical_selected_receipts(
    stored_receipts: tuple[StoredKrSourceReceipt, ...],
    run: KrSourceCollectionRun,
) -> tuple[StoredKrSourceReceipt, ...]:
    if type(stored_receipts) is not tuple:
        raise InvalidKrRawReceiptProjectionError
    if any(type(stored) is not StoredKrSourceReceipt for stored in stored_receipts):
        raise InvalidKrRawReceiptProjectionError
    if any(type(stored.receipt) is not KrSourceReceipt for stored in stored_receipts):
        raise InvalidKrRawReceiptProjectionError
    if any(
        stored.receipt.source_run_id != run.source_run_id
        or stored.receipt.source is not run.source
        for stored in stored_receipts
    ):
        raise InvalidKrRawReceiptProjectionError
    return tuple(sorted(stored_receipts, key=lambda stored: stored.receipt.receipt_id))
