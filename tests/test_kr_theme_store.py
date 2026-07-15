from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

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
from trading_agent.kr_theme_store import (
    InactiveKrThemeWriterError,
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=9)))
PAYLOAD = b'{"title":"synthetic semiconductor catalyst"}'


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
    cycle = _cycle(news_count=1)
    with store.writer() as writer:
        _ = writer.append_catalyst(record, _observation(record), PAYLOAD)
        _ = writer.append_cycle(cycle)
        _ = writer.append_classification(_classification(record))

    tables = (
        "kr_catalysts",
        "kr_catalyst_observations",
        "kr_collection_cycles",
        "kr_theme_classifications",
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
            record_count=news_count if source is KrCatalystSource.NEWS else 0,
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
