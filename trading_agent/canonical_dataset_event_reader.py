from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pyarrow as pa

import trading_agent.canonical_duckdb_replay as replay_module
from trading_agent.canonical_duckdb_replay import (
    CanonicalDatasetReplay,
    CanonicalDatasetReplayError,
    replay_canonical_dataset,
)
from trading_agent.canonical_event_models import CanonicalEventEnvelope


def replay_canonical_dataset_events(
    dataset_directory: Path,
) -> tuple[CanonicalDatasetReplay, tuple[CanonicalEventEnvelope, ...]]:
    try:
        replay = replay_canonical_dataset(dataset_directory)
        events = _read_revalidated_events(dataset_directory, replay)
    except (CanonicalDatasetReplayError, OSError, TypeError, ValueError, duckdb.Error, pa.ArrowException):
        raise CanonicalDatasetReplayError from None
    return replay, events


def _read_revalidated_events(
    dataset_directory: Path,
    expected: CanonicalDatasetReplay,
) -> tuple[CanonicalEventEnvelope, ...]:
    partitions = replay_module._partition_directories(dataset_directory)
    directory_fd = replay_module._open_private_dataset_directory(partitions)
    parquet_fd: int | None = None
    try:
        if set(os.listdir(directory_fd)) != {
            replay_module._EVENTS_NAME,
            replay_module._MANIFEST_NAME,
        }:
            raise ValueError
        sidecar = replay_module._validate_sidecar(
            replay_module._read_private_file(directory_fd, replay_module._MANIFEST_NAME)
        )
        if (
            sidecar.dataset_id != expected.dataset_id
            or tuple(path.name for path in reversed(partitions)) != sidecar.expected_path_components
        ):
            raise ValueError
        parquet_fd = replay_module._open_private_file(directory_fd, replay_module._EVENTS_NAME)
    finally:
        os.close(directory_fd)
    if parquet_fd is None:
        raise ValueError
    try:
        if replay_module._sha256_file_descriptor(parquet_fd) != sidecar.parquet_sha256:
            raise ValueError
        replay_module._validate_parquet_schema(parquet_fd)
        physical_rows = replay_module._canonical_rows_from_duckdb(replay_module._stable_fd_path(parquet_fd))
        if replay_module._sha256_file_descriptor(parquet_fd) != sidecar.parquet_sha256:
            raise ValueError
    finally:
        os.close(parquet_fd)
    canonical_rows = replay_module._revalidate_canonical_rows(physical_rows, sidecar)
    events = tuple(replay_module._event_from_physical_row(row, sidecar) for row in physical_rows)
    if (
        len(events) != sidecar.event_count
        or replay_module._sha256_bytes(replay_module._canonical_json_bytes(canonical_rows))
        != sidecar.canonical_event_content_sha256
        or replay_module._dataset_id(canonical_rows, sidecar) != sidecar.dataset_id
    ):
        raise ValueError
    return events


__all__ = ("replay_canonical_dataset_events",)
