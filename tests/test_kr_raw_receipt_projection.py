from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Never, cast

import pytest

from trading_agent.kr_raw_receipt_projection import (
    InvalidKrRawReceiptProjectionError,
    project_kr_source_run_receipts,
)
from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import (
    KrSourceReceiptProjectionSnapshot,
    KrThemeReader,
    KrThemeStore,
)

CYCLE_ID = "kr-raw-projection-cycle-001"
MARKET_DATE = dt.date(2026, 7, 17)
STARTED_AT = dt.datetime(2026, 7, 17, 9, 30, tzinfo=dt.UTC)
PRIVATE_PAYLOAD = b'{"account":"private-source-account","title":"synthetic"}'


def test_projects_dart_receipts_with_receipt_ledger_high_water_and_redaction(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    _append_receipt_linked_run(
        store,
        _receipt(
            KrCatalystSource.NEWS,
            b'{"prior":true}',
            collection_cycle_id="kr-raw-projection-prior-001",
        ),
        collection_cycle_id="kr-raw-projection-prior-001",
    )
    first = _receipt(
        KrCatalystSource.DART,
        PRIVATE_PAYLOAD,
        request_key="opendart:private-account:page:1",
    )
    second = _receipt(
        KrCatalystSource.DART,
        b'{"page":2}',
        request_key="opendart:private-account:page:2",
        received_at=STARTED_AT + dt.timedelta(seconds=2),
    )
    _append_receipt_linked_run(store, first, second)

    manifest = project_kr_source_run_receipts(
        store,
        collection_cycle_id=CYCLE_ID,
        source=KrCatalystSource.DART,
    )

    assert manifest is not None
    assert manifest.source_id == "kr.opendart"
    assert manifest.market_date == MARKET_DATE
    assert manifest.parent_ledger_generation == 3
    assert tuple(item.receipt_id for item in manifest.receipts) == tuple(
        sorted((first.receipt_id, second.receipt_id))
    )
    references = {item.receipt_id: item for item in manifest.receipts}
    assert references[first.receipt_id].received_at == first.received_at
    assert references[first.receipt_id].payload_sha256 == first.payload_sha256
    assert references[first.receipt_id].byte_size == len(PRIVATE_PAYLOAD)
    assert references[second.receipt_id].received_at == second.received_at
    assert references[second.receipt_id].payload_sha256 == second.payload_sha256
    assert references[second.receipt_id].byte_size == len(b'{"page":2}')
    exported = manifest.model_dump_json()
    assert PRIVATE_PAYLOAD.decode() not in exported
    assert first.source_run_id not in exported
    assert first.request_key not in exported
    assert "private-source-account" not in repr(manifest)


def test_reader_snapshot_selects_terminal_run_receipts_and_rowid_high_water(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    _append_receipt_linked_run(
        store,
        _receipt(
            KrCatalystSource.NEWS,
            b'{"prior":true}',
            collection_cycle_id="kr-raw-projection-prior-001",
        ),
        collection_cycle_id="kr-raw-projection-prior-001",
    )
    first = _receipt(KrCatalystSource.DART, b'{"page":1}')
    second = _receipt(
        KrCatalystSource.DART,
        b'{"page":2}',
        request_key="opendart:fixture:page:2",
        received_at=STARTED_AT + dt.timedelta(seconds=2),
    )
    _append_receipt_linked_run(store, first, second)

    snapshot = store.source_receipt_projection_snapshot(
        collection_cycle_id=CYCLE_ID,
        source=KrCatalystSource.DART,
    )

    assert type(snapshot) is KrSourceReceiptProjectionSnapshot
    assert snapshot.run.source_run_id == first.source_run_id
    assert tuple(item.receipt.receipt_id for item in snapshot.receipts) == (
        first.receipt_id,
        second.receipt_id,
    )
    assert snapshot.parent_ledger_generation == 3


def test_rejects_orphan_receipt_without_matching_successful_run(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _receipt(KrCatalystSource.DART, PRIVATE_PAYLOAD)
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, PRIVATE_PAYLOAD)

    error = _projection_error(store, source=KrCatalystSource.DART)

    assert str(error) == "KR raw receipt projection is invalid"
    assert PRIVATE_PAYLOAD.decode() not in str(error)
    assert receipt.source_run_id not in str(error)
    assert receipt.request_key not in str(error)
    assert error.__cause__ is None


@pytest.mark.parametrize(
    ("status", "collection_date"),
    (
        (KrCoverageStatus.FAILED, MARKET_DATE),
        (KrCoverageStatus.SUCCESS, None),
    ),
)
def test_rejects_failed_or_collection_date_missing_run(
    tmp_path: Path,
    status: KrCoverageStatus,
    collection_date: dt.date | None,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    run = _run(
        KrCatalystSource.DART,
        status=status,
        failure_code="transport_error" if status is KrCoverageStatus.FAILED else None,
        collection_date=collection_date,
    )
    with store.writer() as writer:
        _ = writer.append_source_run(run)

    error = _projection_error(store, source=KrCatalystSource.DART)

    assert str(error) == "KR raw receipt projection is invalid"
    assert run.source_run_id not in str(error)
    assert error.__cause__ is None


def test_rejects_snapshot_for_a_different_requested_source(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _receipt(KrCatalystSource.NEWS, b'{"target":true}')
    _append_receipt_linked_run(store, receipt)
    snapshot = store.source_receipt_projection_snapshot(
        collection_cycle_id=CYCLE_ID,
        source=KrCatalystSource.NEWS,
    )
    assert type(snapshot) is KrSourceReceiptProjectionSnapshot

    error = _projection_error(
        _MismatchedSnapshotReader(store.path, snapshot),
        source=KrCatalystSource.DART,
    )

    assert str(error) == "KR raw receipt projection is invalid"
    assert receipt.source_run_id not in str(error)
    assert error.__cause__ is None


def test_returns_none_for_empty_successful_volume_surge_run(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    with store.writer() as writer:
        _ = writer.append_source_run(_run(KrCatalystSource.VOLUME_SURGE))

    assert (
        project_kr_source_run_receipts(
            store,
            collection_cycle_id=CYCLE_ID,
            source=KrCatalystSource.VOLUME_SURGE,
        )
        is None
    )


@pytest.mark.parametrize(
    "source",
    (
        KrCatalystSource.DART,
        KrCatalystSource.NEWS,
        KrCatalystSource.KIS_RANKING,
    ),
)
def test_rejects_empty_successful_receipt_backed_source_run(
    tmp_path: Path,
    source: KrCatalystSource,
) -> None:
    store = KrThemeStore(tmp_path / f"kr-theme-{source.value}.sqlite3")
    with store.writer() as writer:
        _ = writer.append_source_run(_run(source))

    error = _projection_error(store, source=source)

    assert str(error) == "KR raw receipt projection is invalid"
    assert error.__cause__ is None


def test_rejects_persisted_late_receipt_for_empty_volume_surge_run(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    with store.writer() as writer:
        _ = writer.append_source_run(_run(KrCatalystSource.VOLUME_SURGE))
    late_payload = b'{"private":"late-volume-receipt"}'
    late_receipt = _receipt(
        KrCatalystSource.VOLUME_SURGE,
        late_payload,
        request_key="volume-surge:private:late",
    )
    with store.writer() as writer:
        _ = writer.append_source_receipt(late_receipt, late_payload)

    error = _projection_error(store, source=KrCatalystSource.VOLUME_SURGE)

    assert str(error) == "KR raw receipt projection is invalid"
    assert late_payload.decode() not in str(error)
    assert late_receipt.source_run_id not in str(error)
    assert late_receipt.request_key not in str(error)
    assert error.__cause__ is None


def test_adapter_uses_snapshot_instead_of_legacy_raw_receipt_reads(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _receipt(KrCatalystSource.DART, b'{"target":true}')
    _append_receipt_linked_run(store, receipt)

    manifest = project_kr_source_run_receipts(
        _LegacyRawReadsForbiddenReader(store.path),
        collection_cycle_id=CYCLE_ID,
        source=KrCatalystSource.DART,
    )

    assert manifest is not None
    assert manifest.source_id == "kr.opendart"
    assert manifest.receipt_count == 1


def test_rejects_persisted_late_same_run_receipt(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _receipt(KrCatalystSource.DART, b'{"target":true}')
    _append_receipt_linked_run(store, receipt)
    late_payload = b'{"private":"late-receipt"}'
    late_receipt = _receipt(
        KrCatalystSource.DART,
        late_payload,
        request_key="opendart:private:late",
    )
    with store.writer() as writer:
        _ = writer.append_source_receipt(late_receipt, late_payload)

    error = _projection_error(store, source=KrCatalystSource.DART)

    assert str(error) == "KR raw receipt projection is invalid"
    assert late_payload.decode() not in str(error)
    assert late_receipt.source_run_id not in str(error)
    assert late_receipt.request_key not in str(error)
    assert error.__cause__ is None


@pytest.mark.parametrize(
    ("source", "source_id"),
    (
        (KrCatalystSource.DART, "kr.opendart"),
        (KrCatalystSource.NEWS, "kr.ls.nws"),
        (KrCatalystSource.KIS_RANKING, "kr.kis.ranking"),
        (KrCatalystSource.VOLUME_SURGE, "kr.kis.volume_surge"),
    ),
)
def test_maps_sources_and_keeps_receipt_high_water_stable_after_later_appends(
    tmp_path: Path,
    source: KrCatalystSource,
    source_id: str,
) -> None:
    store = KrThemeStore(tmp_path / f"kr-theme-{source.value}.sqlite3")
    _append_receipt_linked_run(
        store,
        _receipt(
            KrCatalystSource.NEWS,
            b'{"prior":true}',
            collection_cycle_id="kr-raw-projection-prior-001",
        ),
        collection_cycle_id="kr-raw-projection-prior-001",
    )
    receipt = _receipt(source, b'{"target":true}')
    _append_receipt_linked_run(store, receipt)

    before = project_kr_source_run_receipts(
        store,
        collection_cycle_id=CYCLE_ID,
        source=source,
    )

    _append_receipt_linked_run(
        store,
        _receipt(
            KrCatalystSource.KIS_RANKING,
            b'{"later":true}',
            collection_cycle_id="kr-raw-projection-later-001",
        ),
        collection_cycle_id="kr-raw-projection-later-001",
    )
    after = project_kr_source_run_receipts(
        store,
        collection_cycle_id=CYCLE_ID,
        source=source,
    )

    assert before is not None
    assert before.source_id == source_id
    assert before.parent_ledger_generation == 2
    assert after == before


@pytest.mark.parametrize(
    ("reader", "collection_cycle_id", "source"),
    (
        (cast(KrThemeReader, object()), CYCLE_ID, KrCatalystSource.DART),
        (None, "", KrCatalystSource.DART),
        (None, CYCLE_ID, cast(KrCatalystSource, "dart")),
    ),
)
def test_rejects_invalid_public_arguments(
    tmp_path: Path,
    reader: KrThemeReader | None,
    collection_cycle_id: str,
    source: KrCatalystSource,
) -> None:
    actual_reader = KrThemeStore(tmp_path / "kr-theme.sqlite3") if reader is None else reader

    error = _projection_error(
        actual_reader,
        collection_cycle_id=collection_cycle_id,
        source=source,
    )

    assert str(error) == "KR raw receipt projection is invalid"
    assert error.__cause__ is None


def _projection_error(
    reader: KrThemeReader,
    *,
    collection_cycle_id: str = CYCLE_ID,
    source: KrCatalystSource,
) -> InvalidKrRawReceiptProjectionError:
    with pytest.raises(InvalidKrRawReceiptProjectionError) as captured:
        _ = project_kr_source_run_receipts(
            reader,
            collection_cycle_id=collection_cycle_id,
            source=source,
        )
    return captured.value


def _append_receipt_linked_run(
    store: KrThemeStore,
    *receipts: KrSourceReceipt,
    collection_cycle_id: str = CYCLE_ID,
) -> None:
    assert receipts
    source = receipts[0].source
    assert all(receipt.source is source for receipt in receipts)
    run = _run(
        source,
        collection_cycle_id=collection_cycle_id,
        receipt_ids=tuple(sorted(receipt.receipt_id for receipt in receipts)),
    )
    with store.writer() as writer:
        for receipt in receipts:
            _ = writer.append_source_receipt(receipt, _payload_for(receipt))
        _ = writer.append_source_run(run)


def _receipt(
    source: KrCatalystSource,
    payload: bytes,
    *,
    collection_cycle_id: str = CYCLE_ID,
    request_key: str | None = None,
    received_at: dt.datetime = STARTED_AT + dt.timedelta(seconds=1),
) -> KrSourceReceipt:
    return KrSourceReceipt(
        source_run_id=f"{collection_cycle_id}:{source.value}",
        source=source,
        request_key=request_key or f"{source.value}:fixture:page:1",
        received_at=received_at,
        http_status=200,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _run(
    source: KrCatalystSource,
    *,
    collection_cycle_id: str = CYCLE_ID,
    status: KrCoverageStatus = KrCoverageStatus.SUCCESS,
    failure_code: str | None = None,
    collection_date: dt.date | None = MARKET_DATE,
    receipt_ids: tuple[str, ...] = (),
) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id=f"{collection_cycle_id}:{source.value}",
        collection_cycle_id=collection_cycle_id,
        source=source,
        adapter_version=f"{source.value}-fixture-v1",
        started_at=STARTED_AT,
        completed_at=STARTED_AT + dt.timedelta(minutes=1),
        status=status,
        record_count=0,
        failure_code=failure_code,
        receipt_ids=receipt_ids,
        collection_date=collection_date,
    )


def _payload_for(receipt: KrSourceReceipt) -> bytes:
    payloads = {
        hashlib.sha256(b'{"prior":true}').hexdigest(): b'{"prior":true}',
        hashlib.sha256(PRIVATE_PAYLOAD).hexdigest(): PRIVATE_PAYLOAD,
        hashlib.sha256(b'{"page":1}').hexdigest(): b'{"page":1}',
        hashlib.sha256(b'{"page":2}').hexdigest(): b'{"page":2}',
        hashlib.sha256(b'{"target":true}').hexdigest(): b'{"target":true}',
        hashlib.sha256(b'{"later":true}').hexdigest(): b'{"later":true}',
    }
    return payloads[receipt.payload_sha256]


class _LegacyRawReadsForbiddenReader(KrThemeReader):
    __slots__ = ()

    def source_runs(self, collection_cycle_id: str | None = None) -> Never:
        raise AssertionError(collection_cycle_id)

    def source_receipts(self, source_run_id: str | None = None) -> Never:
        raise AssertionError(source_run_id)


class _MismatchedSnapshotReader(KrThemeReader):
    __slots__ = ("_snapshot",)

    def __init__(self, path: Path, snapshot: KrSourceReceiptProjectionSnapshot) -> None:
        super().__init__(path)
        self._snapshot = snapshot

    def source_receipt_projection_snapshot(
        self,
        *,
        collection_cycle_id: str,
        source: KrCatalystSource,
    ) -> KrSourceReceiptProjectionSnapshot | None:
        return self._snapshot
