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


def test_ranking_coverage_records_success_and_failure_without_hiding_gap(
    tmp_path: Path,
) -> None:
    # Given: one ranking request succeeded and one failed at the provider.
    path = tmp_path / "ranking_request_coverage.csv"
    observed_at = dt.datetime(2026, 7, 14, 10, 30, tzinfo=dt.UTC)
    discovery = ranking_journal.RankingDiscovery(
        (
            ranking_journal.RankingGroup(
                ranking_journal.RankingSource.VOLUME,
                "NAS",
                (),
            ),
        ),
        (
            ranking_journal.RankingFailure(
                ranking_journal.RankingSource.UPDOWN,
                "AMS",
                "HTTP 500",
            ),
        ),
    )

    # When: the request coverage is appended to the session audit.
    ranking_journal.append_ranking_coverage(path, observed_at, discovery)

    # Then: an empty successful response and the missing source remain distinct.
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert tuple(row["status"] for row in rows) == ("ok", "failed")
    assert rows[0]["row_count"] == "0"
    assert rows[1]["exchange"] == "AMS"
    assert rows[1]["reason"] == "HTTP 500"
