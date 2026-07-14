from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Final

CYCLE_HEADER: Final = (
    "ranking_observed_at",
    "status",
    "eligible_count",
    "success_count",
    "failure_count",
)
REUSE_CYCLE_HEADER: Final = (
    "ranking_observed_at",
    "status",
    "eligible_count",
    "reused_success_count",
    "attempted_count",
    "new_success_count",
    "failure_count",
)


def repair_transient_cycle_rows(output_dir: Path) -> int:
    cycle_path = output_dir / "kis_opening_gap_cycles.csv"
    if not cycle_path.is_file():
        return 0
    cycle_rows = _read_rows(cycle_path)
    if not cycle_rows or tuple(cycle_rows[0]) != CYCLE_HEADER:
        raise ValueError(f"unexpected opening-gap cycle header: {cycle_path}")

    normalized: list[list[str]] = [list(CYCLE_HEADER)]
    migrated: list[list[str]] = []
    for row in cycle_rows[1:]:
        if len(row) == len(CYCLE_HEADER):
            normalized.append(row)
            continue
        if len(row) != len(REUSE_CYCLE_HEADER):
            raise ValueError(f"unexpected opening-gap cycle row: {row}")
        eligible, reused, attempted, new_success, failure = map(int, row[2:])
        if eligible != reused + attempted or attempted != new_success + failure:
            raise ValueError(f"inconsistent transient opening-gap cycle row: {row}")
        normalized.append(
            [
                row[0],
                row[1],
                row[2],
                str(reused + new_success),
                row[6],
            ]
        )
        migrated.append(row)

    if not migrated:
        return 0
    reuse_path = output_dir / "kis_opening_gap_reuse_cycles.csv"
    reuse_rows = _reuse_rows(reuse_path)
    existing = {row[0]: row for row in reuse_rows[1:]}
    for row in migrated:
        prior = existing.get(row[0])
        if prior is not None and prior != row:
            raise ValueError(f"conflicting opening-gap reuse row: {row[0]}")
        existing[row[0]] = row
    merged_reuse: list[list[str]] = [
        list(REUSE_CYCLE_HEADER),
        *sorted(existing.values()),
    ]

    _write_rows_atomically(reuse_path, merged_reuse)
    _write_rows_atomically(cycle_path, normalized)
    return len(migrated)


def _reuse_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return [list(REUSE_CYCLE_HEADER)]
    rows = _read_rows(path)
    if not rows or tuple(rows[0]) != REUSE_CYCLE_HEADER:
        raise ValueError(f"unexpected opening-gap reuse header: {path}")
    if any(len(row) != len(REUSE_CYCLE_HEADER) for row in rows[1:]):
        raise ValueError(f"unexpected opening-gap reuse row: {path}")
    return rows


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def _write_rows_atomically(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.is_file() else 0o644
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".repairing",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            csv.writer(handle).writerows(rows)
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
