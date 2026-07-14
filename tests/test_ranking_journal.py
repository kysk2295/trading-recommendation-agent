from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from trading_agent import ranking_journal


def test_legacy_snapshot_adds_unknown_selection_input_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "kis_ranking_snapshots.csv"
    legacy_fields = ranking_journal.RANKING_FIELDS[:-1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=legacy_fields)
        writer.writeheader()
        writer.writerow({field: "legacy" for field in legacy_fields})

    ranking_journal.append_ranking_snapshot(
        path,
        ranking_journal.RankingSnapshot(
            dt.datetime(2026, 7, 10, 9, 30, tzinfo=dt.UTC),
            (),
            (),
        ),
    )

    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert rows[0]["selection_input"] == ""
