from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrClassifierKind,
    KrCoverageStatus,
    KrRelatedSymbol,
    KrSourceCoverage,
    KrThemeClassification,
    KrThemeDirection,
    KrThemeRelation,
)
from trading_agent.kr_theme_schema import CREATE_KR_THEME_SCHEMA_V1
from trading_agent.kr_theme_store import (
    InactiveKrThemeWriterError,
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=9)))
PAYLOAD = b'{"title":"synthetic semiconductor catalyst"}'
RAW_DART_PAGE = b'{"status":"000","list":[{"rcept_no":"20260715000001"}]}'
DART_ITEM = b'{"corp_name":"synthetic","rcept_no":"20260715000001"}'


def test_raw_catalyst_is_private_idempotent_and_query_only_readable(tmp_path: Path) -> None:
    path = tmp_path / "kr-theme.sqlite3"
    store = KrThemeStore(path)
    record = _record(PAYLOAD)
    observation = _observation(record)

    with store.writer() as writer:
        first = writer.append_catalyst(record, observation, PAYLOAD)
        second = writer.append_catalyst(record, observation, PAYLOAD)

    assert first.catalyst_inserted is True
    assert first.observation_inserted is True
    assert second.catalyst_inserted is False
    assert second.observation_inserted is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{path}.writer.lock").stat().st_mode) == 0o600
    stored = store.catalysts()
    assert len(stored) == 1
    assert stored[0].record == record
    assert stored[0].raw_payload == PAYLOAD
    assert "raw_payload" not in repr(stored[0])
    assert "synthetic semiconductor catalyst" not in repr(stored[0])
    assert store.observations() == (observation,)
    with store.reader_connection() as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            _ = connection.execute("DELETE FROM kr_catalysts")


def test_recapture_adds_a_cycle_observation_without_duplicating_raw(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    first_record = _record(PAYLOAD)
    first_observation = _observation(first_record)
    later_at = OBSERVED_AT + dt.timedelta(minutes=5)
    later_record = _record(PAYLOAD, first_observed_at=later_at)
    later_observation = _observation(
        later_record,
        cycle_id="kr-cycle-002",
        observed_at=later_at,
    )

    with store.writer() as writer:
        _ = writer.append_catalyst(first_record, first_observation, PAYLOAD)
        result = writer.append_catalyst(later_record, later_observation, PAYLOAD)

    assert result.catalyst_inserted is False
    assert result.observation_inserted is True
    assert len(store.catalysts()) == 1
    assert store.catalysts()[0].record.first_observed_at == OBSERVED_AT
    assert store.observations() == (first_observation, later_observation)


def test_same_source_identity_rejects_changed_payload_or_earlier_observation(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    record = _record(PAYLOAD)
    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), PAYLOAD)
        changed = b'{"title":"changed"}'
        changed_record = _record(changed)
        with pytest.raises(KrThemeConflictError):
            _ = writer.append_catalyst(
                changed_record,
                _observation(changed_record),
                changed,
            )
        earlier_at = OBSERVED_AT - dt.timedelta(seconds=1)
        earlier_record = _record(
            PAYLOAD,
            published_at=OBSERVED_AT - dt.timedelta(minutes=2),
            first_observed_at=earlier_at,
        )
        with pytest.raises(KrThemeConflictError):
            _ = writer.append_catalyst(
                earlier_record,
                _observation(earlier_record, observed_at=earlier_at),
                PAYLOAD,
            )


def test_cycle_finalization_requires_exact_observed_source_counts(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    record = _record(PAYLOAD)
    cycle = _cycle(news_count=1)

    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), PAYLOAD)
        assert writer.append_cycle(cycle) is True
        assert writer.append_cycle(cycle) is False

    assert store.cycles() == (cycle,)
    assert store.cycles()[0].complete is True

    mismatch = _cycle(cycle_id="kr-cycle-mismatch", news_count=1)
    with store.writer() as writer, pytest.raises(InvalidKrThemeSourceError):
        _ = writer.append_cycle(mismatch)


def test_failed_source_cycle_is_preserved_as_incomplete(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    incomplete = _cycle(
        news_count=0,
        failed_source=KrCatalystSource.NEWS,
        failure_code="http_503",
    )

    with store.writer() as writer:
        assert writer.append_cycle(incomplete) is True

    assert store.cycles()[0].complete is False
    assert store.cycles()[0].coverage[2].failure_code == "http_503"


def test_classification_requires_causal_existing_catalyst_and_is_immutable(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    record = _record(PAYLOAD)
    classification = _classification(record)

    with store.writer() as writer:
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_classification(classification)
        _ = writer.append_catalyst(record, _observation(record), PAYLOAD)
        too_early = _classification(
            record,
            classified_at=OBSERVED_AT - dt.timedelta(seconds=1),
        )
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_classification(too_early)
        assert writer.append_classification(classification) is True
        assert writer.append_classification(classification) is False
        changed = KrThemeClassification.model_validate(
            classification.model_dump(mode="python") | {"confidence": Decimal("0.8")}
        )
        with pytest.raises(KrThemeConflictError):
            _ = writer.append_classification(changed)
        repeat = _classification(record, run_id="stability-001")
        assert writer.append_classification(repeat) is True

    assert store.classifications() == (classification, repeat)


def test_all_tables_reject_update_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "kr-theme.sqlite3"
    store = KrThemeStore(path)
    record = _record(PAYLOAD)
    dart_record = _dart_record(DART_ITEM)
    receipt = _source_receipt(RAW_DART_PAGE)
    cycle = _cycle(news_count=1, dart_count=1)
    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), PAYLOAD)
        _ = writer.append_source_receipt(receipt, RAW_DART_PAGE)
        _ = writer.append_catalyst_from_receipt(
            dart_record,
            _observation(dart_record),
            DART_ITEM,
            receipt_id=receipt.receipt_id,
            item_index=0,
        )
        _ = writer.append_cycle(cycle)
        _ = writer.append_classification(_classification(record))
        _ = writer.append_source_run(_source_run(receipt, record_count=1))

    tables = (
        "kr_catalysts",
        "kr_catalyst_observations",
        "kr_collection_cycles",
        "kr_theme_classifications",
        "kr_source_receipts",
        "kr_catalyst_observation_receipts",
        "kr_source_collection_runs",
    )
    with sqlite3.connect(path) as connection:
        for table in tables:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                _ = connection.execute(f"UPDATE {table} SET rowid = rowid")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                _ = connection.execute(f"DELETE FROM {table}")


def test_single_writer_lease_and_inactive_writer_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "kr-theme.sqlite3"
    store = KrThemeStore(path)
    with (
        store.writer() as writer,
        pytest.raises(KrThemeWriterLeaseUnavailableError),
        KrThemeStore(path).writer(),
    ):
        pass
    with pytest.raises(InactiveKrThemeWriterError):
        _ = writer.append_cycle(_cycle(news_count=0))


def test_reader_detects_blob_checksum_tampering(tmp_path: Path) -> None:
    path = tmp_path / "kr-theme.sqlite3"
    store = KrThemeStore(path)
    record = _record(PAYLOAD)
    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), PAYLOAD)
    with sqlite3.connect(path) as connection:
        _ = connection.execute("DROP TRIGGER kr_catalysts_no_update")
        _ = connection.execute(
            "UPDATE kr_catalysts SET payload_blob = ? WHERE catalyst_id = ?",
            (b"tampered", record.catalyst_id),
        )

    with pytest.raises(InvalidKrThemeSourceError):
        _ = store.catalysts()


def test_v1_ledger_migrates_without_rewriting_existing_catalysts(tmp_path: Path) -> None:
    path = tmp_path / "kr-theme-v1.sqlite3"
    record = _record(PAYLOAD)
    with sqlite3.connect(path) as connection:
        connection.executescript(CREATE_KR_THEME_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version = 1")
        _ = connection.execute(
            "INSERT INTO kr_catalysts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.catalyst_id,
                record.source.value,
                record.source_record_id,
                record.publisher_id,
                record.published_at.isoformat() if record.published_at else None,
                record.first_observed_at.isoformat(),
                record.content_type,
                record.payload_sha256,
                PAYLOAD,
            ),
        )

    store = KrThemeStore(path)
    with store.writer():
        pass

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert store.catalysts()[0].record == record
    assert {
        "kr_source_receipts",
        "kr_catalyst_observation_receipts",
        "kr_source_collection_runs",
    } <= tables


def test_source_receipt_catalyst_lineage_and_run_are_idempotent(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _source_receipt(RAW_DART_PAGE)
    record = _dart_record(DART_ITEM)
    observation = _observation(record)
    run = _source_run(receipt, record_count=1)

    with store.writer() as writer:
        first_receipt = writer.append_source_receipt(receipt, RAW_DART_PAGE)
        later_receipt = KrSourceReceipt.model_validate(
            receipt.model_dump(mode="python")
            | {"received_at": receipt.received_at + dt.timedelta(seconds=5)}
        )
        second_receipt = writer.append_source_receipt(later_receipt, RAW_DART_PAGE)
        first = writer.append_catalyst_from_receipt(
            record,
            observation,
            DART_ITEM,
            receipt_id=receipt.receipt_id,
            item_index=0,
        )
        second = writer.append_catalyst_from_receipt(
            record,
            observation,
            DART_ITEM,
            receipt_id=receipt.receipt_id,
            item_index=0,
        )
        assert writer.append_source_run(run) is True
        assert writer.append_source_run(run) is False

    assert first_receipt.receipt_inserted is True
    assert second_receipt.receipt_inserted is False
    assert second_receipt.stored.receipt.received_at == receipt.received_at
    assert first.catalyst_inserted is True
    assert first.observation_inserted is True
    assert second.catalyst_inserted is False
    assert second.observation_inserted is False
    assert store.source_receipts() == (first_receipt.stored,)
    assert len(store.observation_receipts()) == 1
    assert store.observation_receipts()[0].receipt_id == receipt.receipt_id
    assert store.source_runs() == (run,)
    assert store.source_runs(collection_cycle_id="missing") == ()


def test_source_receipt_rejects_changed_or_earlier_response(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _source_receipt(RAW_DART_PAGE)
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, RAW_DART_PAGE)
        changed_payload = b'{"status":"013","message":"none"}'
        changed = _source_receipt(changed_payload)
        with pytest.raises(KrThemeConflictError):
            _ = writer.append_source_receipt(changed, changed_payload)
        earlier = KrSourceReceipt.model_validate(
            receipt.model_dump(mode="python")
            | {"received_at": receipt.received_at - dt.timedelta(seconds=1)}
        )
        with pytest.raises(KrThemeConflictError):
            _ = writer.append_source_receipt(earlier, RAW_DART_PAGE)


def test_receipt_lineage_rejects_wrong_source_time_and_item_payload(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _source_receipt(RAW_DART_PAGE)
    dart_record = _dart_record(DART_ITEM)
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, RAW_DART_PAGE)
        news_record = _record(PAYLOAD)
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_catalyst_from_receipt(
                news_record,
                _observation(news_record),
                PAYLOAD,
                receipt_id=receipt.receipt_id,
                item_index=0,
            )
        before_receipt = receipt.received_at - dt.timedelta(seconds=1)
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_catalyst_from_receipt(
                _dart_record(DART_ITEM, first_observed_at=before_receipt),
                _observation(dart_record, observed_at=before_receipt),
                DART_ITEM,
                receipt_id=receipt.receipt_id,
                item_index=0,
            )
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_catalyst_from_receipt(
                _dart_record(b'{"changed":true}'),
                _observation(dart_record),
                DART_ITEM,
                receipt_id=receipt.receipt_id,
                item_index=0,
            )


def test_source_run_requires_exact_receipts_observations_and_links(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    receipt = _source_receipt(RAW_DART_PAGE)
    record = _dart_record(DART_ITEM)
    observation = _observation(record)
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, RAW_DART_PAGE)
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_source_run(_source_run(receipt, record_count=1))
        _ = writer.append_catalyst(record, observation, DART_ITEM)
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_source_run(_source_run(receipt, record_count=1))

    linked_store = KrThemeStore(tmp_path / "kr-theme-linked.sqlite3")
    with linked_store.writer() as writer:
        _ = writer.append_source_receipt(receipt, RAW_DART_PAGE)
        _ = writer.append_catalyst_from_receipt(
            record,
            observation,
            DART_ITEM,
            receipt_id=receipt.receipt_id,
            item_index=0,
        )
        missing_receipt = KrSourceCollectionRun.model_validate(
            _source_run(receipt, record_count=1).model_dump(mode="python")
            | {"receipt_ids": ()}
        )
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_source_run(missing_receipt)


def test_receipt_free_derived_volume_source_run_is_allowed(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    payload = b'{"schema_version":2,"symbols":[]}'
    record = _direct_source_record(KrCatalystSource.VOLUME_SURGE, payload)
    run = _receipt_free_source_run(KrCatalystSource.VOLUME_SURGE, record_count=1)

    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), payload)
        assert writer.append_source_run(run) is True
        assert writer.append_source_run(run) is False

    assert store.source_runs() == (run,)
    assert store.observation_receipts() == ()


@pytest.mark.parametrize(
    "source",
    (
        KrCatalystSource.NEWS,
        KrCatalystSource.DART,
        KrCatalystSource.KIS_RANKING,
    ),
)
def test_receipt_free_provider_source_runs_remain_rejected(
    tmp_path: Path,
    source: KrCatalystSource,
) -> None:
    store = KrThemeStore(tmp_path / f"kr-theme-{source.value}.sqlite3")
    payload = b'{"synthetic":true}'
    record = _direct_source_record(source, payload)

    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), payload)
        with pytest.raises(InvalidKrThemeSourceError):
            _ = writer.append_source_run(_receipt_free_source_run(source, record_count=1))


def test_reader_detects_source_receipt_blob_tampering(tmp_path: Path) -> None:
    path = tmp_path / "kr-theme.sqlite3"
    store = KrThemeStore(path)
    receipt = _source_receipt(RAW_DART_PAGE)
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, RAW_DART_PAGE)
    with sqlite3.connect(path) as connection:
        _ = connection.execute("DROP TRIGGER kr_source_receipts_no_update")
        _ = connection.execute(
            "UPDATE kr_source_receipts SET payload_blob = ? WHERE receipt_id = ?",
            (b"tampered", receipt.receipt_id),
        )

    with pytest.raises(InvalidKrThemeSourceError):
        _ = store.source_receipts()


def _record(
    payload: bytes,
    *,
    published_at: dt.datetime = OBSERVED_AT - dt.timedelta(minutes=1),
    first_observed_at: dt.datetime = OBSERVED_AT,
) -> KrCatalystRecord:
    return KrCatalystRecord(
        source=KrCatalystSource.NEWS,
        source_record_id="news://synthetic/001",
        publisher_id="synthetic_news",
        published_at=published_at,
        first_observed_at=first_observed_at,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _dart_record(
    payload: bytes,
    *,
    first_observed_at: dt.datetime = OBSERVED_AT,
) -> KrCatalystRecord:
    return KrCatalystRecord(
        source=KrCatalystSource.DART,
        source_record_id="opendart://disclosure/20260715000001",
        publisher_id="00123456",
        published_at=None,
        first_observed_at=first_observed_at,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _source_receipt(payload: bytes) -> KrSourceReceipt:
    return KrSourceReceipt(
        source_run_id="kr-cycle-001:dart",
        source=KrCatalystSource.DART,
        request_key="opendart:list:20260715:page:1",
        received_at=OBSERVED_AT,
        http_status=200,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _source_run(
    receipt: KrSourceReceipt,
    *,
    record_count: int,
) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id=receipt.source_run_id,
        collection_cycle_id="kr-cycle-001",
        source=receipt.source,
        adapter_version="opendart-list-v1",
        started_at=receipt.received_at,
        completed_at=receipt.received_at,
        status=KrCoverageStatus.SUCCESS,
        record_count=record_count,
        failure_code=None,
        receipt_ids=(receipt.receipt_id,),
    )


def _direct_source_record(
    source: KrCatalystSource,
    payload: bytes,
) -> KrCatalystRecord:
    return KrCatalystRecord(
        source=source,
        source_record_id=f"{source.value}://synthetic/direct-001",
        publisher_id="synthetic",
        published_at=None,
        first_observed_at=OBSERVED_AT,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _receipt_free_source_run(
    source: KrCatalystSource,
    *,
    record_count: int,
) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id=f"kr-cycle-001:{source.value}",
        collection_cycle_id="kr-cycle-001",
        source=source,
        adapter_version=f"synthetic-{source.value}-v1",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT,
        status=KrCoverageStatus.SUCCESS,
        record_count=record_count,
        failure_code=None,
        receipt_ids=(),
        collection_date=OBSERVED_AT.date(),
    )


def _observation(
    record: KrCatalystRecord,
    *,
    cycle_id: str = "kr-cycle-001",
    observed_at: dt.datetime = OBSERVED_AT,
) -> KrCatalystObservation:
    return KrCatalystObservation(
        collection_cycle_id=cycle_id,
        catalyst_id=record.catalyst_id,
        observed_at=observed_at,
    )


def _cycle(
    *,
    cycle_id: str = "kr-cycle-001",
    news_count: int,
    dart_count: int = 0,
    failed_source: KrCatalystSource | None = None,
    failure_code: str | None = None,
) -> KrCatalystCollectionCycle:
    coverage = tuple(
        KrSourceCoverage(
            source=source,
            status=(
                KrCoverageStatus.FAILED
                if source is failed_source
                else KrCoverageStatus.SUCCESS
            ),
            record_count=(
                news_count
                if source is KrCatalystSource.NEWS
                else dart_count
                if source is KrCatalystSource.DART
                else 0
            ),
            failure_code=failure_code if source is failed_source else None,
        )
        for source in sorted(KrCatalystSource, key=lambda item: item.value)
    )
    return KrCatalystCollectionCycle(
        collection_cycle_id=cycle_id,
        started_at=OBSERVED_AT - dt.timedelta(minutes=1),
        completed_at=OBSERVED_AT + dt.timedelta(minutes=1),
        coverage=coverage,
    )


def _classification(
    record: KrCatalystRecord,
    *,
    classified_at: dt.datetime = OBSERVED_AT + dt.timedelta(seconds=10),
    run_id: str = "primary",
) -> KrThemeClassification:
    return KrThemeClassification(
        catalyst_id=record.catalyst_id,
        classifier_kind=KrClassifierKind.KEYWORD,
        classifier_version="kr-keyword-v1",
        prompt_version="no-prompt-v1",
        classification_run_id=run_id,
        classified_at=classified_at,
        direction=KrThemeDirection.POSITIVE,
        confidence=Decimal("0.9"),
        evidence_quote="합성 반도체 공급망 기사",
        theme_name="반도체",
        related_symbols=(
            KrRelatedSymbol(
                symbol="005930",
                relation=KrThemeRelation.DIRECT_BUSINESS,
                rationale="합성 기사 직접 언급",
            ),
        ),
    )
