from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import run_canonical_event_history as history_cli
from trading_agent.canonical_dataset_models import CanonicalDatasetBatch, CanonicalDatasetPartition
from trading_agent.canonical_event_history import (
    CanonicalEventHistoryError,
    replay_canonical_event_history,
)
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.canonical_parquet_writer import write_canonical_dataset_parquet
from trading_agent.data_capability_models import DataSourceId
from trading_agent.raw_object_manifest_models import RawReceipt, RawReceiptPayload
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.security_master_models import DataMarketDomain

UTC = dt.UTC
MARKET_DATE = dt.date(2026, 7, 17)
BASE_TIME = dt.datetime(2026, 7, 17, 14, 30, tzinfo=UTC)
SOURCE = DataSourceId(provider="fixture", feed="news")
PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_canonical_event_history.py"
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)
ENTITY_REFS = (
    CanonicalEntityRef(entity_type=CanonicalEntityType.INSTRUMENT, entity_id="us-eq-fixture-0001"),
    CanonicalEntityRef(entity_type=CanonicalEntityType.ORGANIZATION, entity_id="issuer-fixture-0001"),
)


def test_replays_original_correction_and_tombstone_as_of_time(tmp_path: Path) -> None:
    original = _event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=0)
    correction = _event(
        "event-0002",
        operation=CanonicalEventOperation.CORRECTION,
        correction_of=original.event_id,
        minute=1,
    )
    tombstone = _event(
        "event-0003",
        operation=CanonicalEventOperation.TOMBSTONE,
        correction_of=correction.event_id,
        minute=2,
    )
    datasets = tuple(
        _publish(tmp_path, event, index=index) for index, event in enumerate((original, correction, tombstone), start=1)
    )

    before_correction = replay_canonical_event_history(datasets, as_of=BASE_TIME + dt.timedelta(seconds=30))
    corrected = replay_canonical_event_history(datasets, as_of=BASE_TIME + dt.timedelta(minutes=1, seconds=30))
    deleted = replay_canonical_event_history(datasets, as_of=BASE_TIME + dt.timedelta(minutes=3))

    assert tuple(event.event_id for event in before_correction.active_events) == ("event-0001",)
    assert before_correction.observed_event_count == 1
    assert tuple(event.event_id for event in corrected.active_events) == ("event-0002",)
    assert corrected.superseded_event_ids == ("event-0001",)
    assert corrected.tombstoned_root_event_ids == ()
    assert deleted.active_events == ()
    assert deleted.superseded_event_ids == ("event-0001", "event-0002")
    assert deleted.tombstoned_root_event_ids == ("event-0001",)
    assert deleted.dataset_ids == tuple(sorted(set(deleted.dataset_ids)))
    with pytest.raises(FrozenInstanceError):
        deleted.observed_event_count = 99  # type: ignore[misc]


def test_exact_duplicate_dataset_is_idempotent_but_conflicting_event_id_fails(tmp_path: Path) -> None:
    original = _event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=0)
    first = _publish(tmp_path, original, index=1)

    replay = replay_canonical_event_history((first, first), as_of=BASE_TIME + dt.timedelta(minutes=1))

    assert replay.observed_event_count == 1
    assert len(replay.dataset_ids) == 1

    conflicting = _publish(
        tmp_path,
        _event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=1),
        index=2,
    )
    with pytest.raises(CanonicalEventHistoryError):
        replay_canonical_event_history((first, conflicting), as_of=BASE_TIME + dt.timedelta(minutes=2))


@pytest.mark.parametrize(
    "scenario",
    ("missing", "branched", "reverse_time"),
)
def test_rejects_missing_branched_or_reverse_time_correction_chain(
    tmp_path: Path,
    scenario: str,
) -> None:
    original_minute = 1 if scenario == "reverse_time" else 0
    correction_target = "missing-event" if scenario == "missing" else "event-0001"
    correction_minute = 0 if scenario == "reverse_time" else 1
    events = (
        ()
        if scenario == "missing"
        else (_event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=original_minute),)
    ) + (
        _event(
            "event-0002",
            operation=CanonicalEventOperation.CORRECTION,
            correction_of=correction_target,
            minute=correction_minute,
        ),
    )
    if scenario == "branched":
        events += (
            _event(
                "event-0003",
                operation=CanonicalEventOperation.TOMBSTONE,
                correction_of="event-0001",
                minute=2,
            ),
        )
    datasets = tuple(_publish(tmp_path, event, index=index) for index, event in enumerate(events, start=1))

    with pytest.raises(CanonicalEventHistoryError, match="canonical event history could not be replayed"):
        replay_canonical_event_history(datasets, as_of=BASE_TIME + dt.timedelta(minutes=10))


@pytest.mark.parametrize("mutation", ("source", "entity", "provider", "event_type"))
def test_rejects_correction_that_changes_event_identity(tmp_path: Path, mutation: str) -> None:
    original = _event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=0)
    payload = _event(
        "event-0002",
        operation=CanonicalEventOperation.CORRECTION,
        correction_of=original.event_id,
        minute=1,
    ).model_dump(mode="python")
    if mutation == "source":
        payload["source_id"] = DataSourceId(provider="other", feed="news")
    elif mutation == "entity":
        payload["entity_refs"] = (CanonicalEntityRef(entity_type=CanonicalEntityType.TOPIC, entity_id="other-topic"),)
    elif mutation == "provider":
        payload["provider_event_id"] = "provider-other"
    else:
        payload["event_type"] = "news_delete"
    correction = CanonicalEventEnvelope.model_validate(payload)
    datasets = (
        _publish(tmp_path, original, index=1),
        _publish(tmp_path, correction, index=2),
    )

    with pytest.raises(CanonicalEventHistoryError):
        replay_canonical_event_history(datasets, as_of=BASE_TIME + dt.timedelta(minutes=2))


def test_rejects_naive_as_of_empty_history_and_non_path_inputs(tmp_path: Path) -> None:
    dataset = _publish(
        tmp_path,
        _event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=0),
        index=1,
    )

    for datasets, as_of in (
        ((), BASE_TIME),
        ((dataset,), BASE_TIME.replace(tzinfo=None)),
        ((str(dataset),), BASE_TIME),
    ):
        with pytest.raises(CanonicalEventHistoryError):
            replay_canonical_event_history(datasets, as_of=as_of)  # type: ignore[arg-type]


def test_history_cli_help_bad_input_and_tombstone_happy_path(tmp_path: Path) -> None:
    help_result = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )
    assert help_result.returncode == 0
    assert "--dataset" in help_result.stdout
    assert "--as-of" in help_result.stdout
    assert "--arm" not in help_result.stdout

    blocked_output = tmp_path / "blocked"
    blocked = history_cli.main(
        (
            "--dataset",
            str(tmp_path / "missing"),
            "--as-of",
            (BASE_TIME + dt.timedelta(minutes=3)).isoformat(),
            "--output-dir",
            str(blocked_output),
        )
    )
    assert blocked == 1
    assert "결과: blocked" in (blocked_output / history_cli.REPORT_NAME).read_text()

    original = _event("event-0001", operation=CanonicalEventOperation.ORIGINAL, minute=0)
    tombstone = _event(
        "event-0002",
        operation=CanonicalEventOperation.TOMBSTONE,
        correction_of=original.event_id,
        minute=1,
    )
    datasets = (_publish(tmp_path, original, index=1), _publish(tmp_path, tombstone, index=2))
    output = tmp_path / "ready"
    ready = history_cli.main(
        (
            "--dataset",
            str(datasets[0]),
            "--dataset",
            str(datasets[1]),
            "--as-of",
            (BASE_TIME + dt.timedelta(minutes=2)).isoformat(),
            "--output-dir",
            str(output),
        )
    )
    report_path = output / history_cli.REPORT_NAME
    report = report_path.read_text()
    assert ready == 0
    assert "결과: ready" in report
    assert "verified dataset: 2" in report
    assert "observed event: 2" in report
    assert "active event: 0" in report
    assert "tombstoned root: 1" in report
    assert str(tmp_path) not in report
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def _event(
    event_id: str,
    *,
    operation: CanonicalEventOperation,
    minute: int,
    correction_of: str | None = None,
) -> CanonicalEventEnvelope:
    observed_at = BASE_TIME + dt.timedelta(minutes=minute)
    return CanonicalEventEnvelope(
        event_id=event_id,
        source_id=SOURCE,
        provider_event_id="provider-event-0001",
        entity_refs=ENTITY_REFS,
        event_type="news_item",
        event_time=BASE_TIME,
        published_at=BASE_TIME,
        provider_time=observed_at,
        received_at=observed_at,
        normalized_at=observed_at,
        sequence_or_offset=str(minute + 1),
        operation=operation,
        correction_of=correction_of,
        raw_receipt_ref="a" * 64,
        content_hash=f"{minute + 1:064x}",
        quality_flags=("fixture",),
    )


def _publish(tmp_path: Path, event: CanonicalEventEnvelope, *, index: int) -> Path:
    raw_payload = f"payload-{index}-{event.event_id}".encode()
    receipt_id = hashlib.sha256(f"receipt-{index}-{event.event_id}".encode()).hexdigest()
    receipt = RawReceipt.from_payload(
        receipt_id=receipt_id,
        source_id="fixture.news",
        market_date=MARKET_DATE,
        received_at=event.received_at,
        payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
        payload=RawReceiptPayload(raw_payload),
    )
    manifest = project_raw_receipt_partition(
        (receipt,),
        source_id="fixture.news",
        market_date=MARKET_DATE,
        parent_ledger_generation=index,
    )
    batch = CanonicalDatasetBatch(
        partition=CanonicalDatasetPartition(
            source_id=event.source_id,
            market_domain=DataMarketDomain.US_EQUITIES,
            event_type=event.event_type,
            market_date=MARKET_DATE,
        ),
        raw_manifest=manifest,
        events=(
            CanonicalEventEnvelope.model_validate({**event.model_dump(mode="python"), "raw_receipt_ref": receipt_id}),
        ),
    )
    return write_canonical_dataset_parquet(
        batch,
        output_root=tmp_path / f"canonical-{index}",
    ).dataset_directory


def _execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
