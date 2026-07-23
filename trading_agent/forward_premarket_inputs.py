from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, override

from pydantic import BaseModel, ConfigDict

from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.ranking_journal import (
    RANKING_COVERAGE_FIELDS,
    RANKING_FIELDS,
    RankingSource,
)

MAX_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
WATCH_FILE: Final = "premarket_watch_cycles.csv"
COVERAGE_FILE: Final = "premarket_ranking_request_coverage.csv"
SNAPSHOT_FILE: Final = "premarket_ranking_snapshots.csv"
RISK_FILE: Final = "premarket_risk_screen.csv"
PREMARKET_FILES: Final = (
    WATCH_FILE,
    COVERAGE_FILE,
    SNAPSHOT_FILE,
    RISK_FILE,
)
_WATCH_HEADER: Final = ("started_at", "exit_code", "status")


class PremarketWatchRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    started_at: dt.datetime
    exit_code: int
    status: Literal["ok", "failed"]


class PremarketCoverageRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: dt.datetime
    ranking_source: RankingSource
    exchange: Literal["NAS", "NYS", "AMS"]
    status: Literal["ok", "failed"]
    row_count: str
    reason: str


class PremarketSnapshotRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: dt.datetime
    ranking_source: RankingSource
    exchange: Literal["NAS", "NYS", "AMS"]
    symbol: str
    selected: bool
    selection_input: bool


class PremarketRiskRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: dt.datetime
    exchange: Literal["NAS", "NYS", "AMS"]
    symbol: str
    selected: bool


@dataclass(frozen=True, slots=True)
class PremarketInputs:
    watch: tuple[PremarketWatchRow, ...]
    coverage: tuple[PremarketCoverageRow, ...]
    snapshots: tuple[PremarketSnapshotRow, ...]
    risks: tuple[PremarketRiskRow, ...]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class PremarketInputError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def load_premarket_inputs(session: Path) -> PremarketInputs:
    artifacts = {
        WATCH_FILE: _read_csv(session / WATCH_FILE, _WATCH_HEADER),
        COVERAGE_FILE: _read_csv(
            session / COVERAGE_FILE,
            RANKING_COVERAGE_FIELDS,
        ),
        SNAPSHOT_FILE: _read_csv(
            session / SNAPSHOT_FILE,
            RANKING_FIELDS,
        ),
        RISK_FILE: _read_csv(session / RISK_FILE, MARKET_RISK_HEADER),
    }
    try:
        watch = tuple(
            PremarketWatchRow.model_validate(row)
            for row in artifacts[WATCH_FILE][0]
        )
        coverage = tuple(
            PremarketCoverageRow.model_validate(row)
            for row in artifacts[COVERAGE_FILE][0]
        )
        snapshots = tuple(
            PremarketSnapshotRow.model_validate(row)
            for row in artifacts[SNAPSHOT_FILE][0]
        )
        risks = tuple(
            PremarketRiskRow.model_validate(row)
            for row in artifacts[RISK_FILE][0]
        )
    except (TypeError, ValueError):
        raise PremarketInputError("invalid_readiness_rows") from None
    input_sha256 = hashlib.sha256(
        "|".join(
            f"{name}:{artifacts[name][1]}"
            for name in PREMARKET_FILES
        ).encode()
    ).hexdigest()
    return PremarketInputs(
        watch,
        coverage,
        snapshots,
        risks,
        input_sha256,
    )


def _read_csv(
    path: Path,
    expected_header: tuple[str, ...],
) -> tuple[tuple[dict[str, str], ...], str]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_ARTIFACT_BYTES
            ):
                raise PremarketInputError(
                    f"artifact_not_private:{path.name}"
                )
            with os.fdopen(os.dup(descriptor), "rb") as handle:
                payload = handle.read(MAX_ARTIFACT_BYTES + 1)
            after = os.fstat(descriptor)
            if (
                len(payload) > MAX_ARTIFACT_BYTES
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise PremarketInputError(
                    f"artifact_unstable:{path.name}"
                )
        finally:
            os.close(descriptor)
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))
        if tuple(reader.fieldnames or ()) != expected_header:
            raise PremarketInputError(
                f"artifact_schema_mismatch:{path.name}"
            )
        rows = tuple(dict(row) for row in reader)
        return rows, hashlib.sha256(payload).hexdigest()
    except PremarketInputError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError):
        raise PremarketInputError(
            f"artifact_unreadable:{path.name}"
        ) from None


__all__ = (
    "PremarketCoverageRow",
    "PremarketInputError",
    "PremarketInputs",
    "PremarketRiskRow",
    "PremarketSnapshotRow",
    "load_premarket_inputs",
)
