from __future__ import annotations

import csv
import datetime as dt
import subprocess
from dataclasses import dataclass
from pathlib import Path

from trading_agent import candidate_persistence
from trading_agent.market_risk import MARKET_RISK_HEADER, PORTFOLIO_LIMIT_REASON


@dataclass(frozen=True, slots=True)
class PersistenceFixture:
    observed_at: dt.datetime
    exchange: str
    symbol: str
    selected: bool
    reason: str
    change_pct: float


def test_persistence_uses_full_risk_population_between_consecutive_snapshots(
    tmp_path: Path,
) -> None:
    # Given: three snapshots containing selected, portfolio-limit, and rejected rows.
    screen = tmp_path / "risk.csv"
    start = dt.datetime(2026, 7, 13, 6, 0, tzinfo=dt.UTC)
    _write_screen(
        screen,
        (
            PersistenceFixture(start, "BAQ", "AAA", True, "", 0.20),
            PersistenceFixture(start, "BAQ", "BBB", False, PORTFOLIO_LIMIT_REASON, 0.10),
            PersistenceFixture(start, "BAQ", "WIDE", False, "스프레드 초과", 0.30),
            PersistenceFixture(start + dt.timedelta(minutes=5), "NAS", "AAA", True, "", 0.18),
            PersistenceFixture(start + dt.timedelta(minutes=5), "NAS", "CCC", True, "", 0.15),
            PersistenceFixture(start + dt.timedelta(minutes=5), "NAS", "WIDE", True, "", 0.25),
            PersistenceFixture(
                start + dt.timedelta(minutes=10),
                "NAS",
                "AAA",
                False,
                PORTFOLIO_LIMIT_REASON,
                0.16,
            ),
            PersistenceFixture(start + dt.timedelta(minutes=10), "NAS", "BBB", True, "", 0.12),
        ),
    )

    # When: persistence is measured on the complete risk-screen population.
    result = candidate_persistence.analyze_candidate_persistence(screen)

    # Then: only true risk-eligible rows enter transition sets and venues are canonicalized.
    assert result.summary.snapshot_count == 3
    assert result.summary.candidate_count == 4
    assert result.summary.transition_count == 2
    assert result.transitions[0].source_eligible_count == 2
    assert result.transitions[0].destination_eligible_count == 3
    assert result.transitions[0].continued_count == 1
    assert result.transitions[0].continuation_rate == 0.5
    assert result.transitions[0].jaccard == 0.25
    assert result.transitions[1].continuation_rate == 1 / 3
    assert result.transitions[1].jaccard == 0.25
    by_symbol = {row.symbol: row for row in result.candidates}
    assert by_symbol["AAA"].canonical_exchange == "NAS"
    assert by_symbol["AAA"].eligible_snapshot_count == 3
    assert by_symbol["AAA"].selected_snapshot_count == 2
    assert by_symbol["WIDE"].observed_snapshot_count == 2
    assert by_symbol["WIDE"].eligible_snapshot_count == 1


def test_candidate_persistence_cli_writes_csv_and_korean_report(
    tmp_path: Path,
) -> None:
    # Given: a real risk-screen CSV with one snapshot.
    screen = tmp_path / "risk.csv"
    _write_screen(
        screen,
        (
            PersistenceFixture(
                dt.datetime(2026, 7, 13, 6, 0, tzinfo=dt.UTC),
                "BAQ",
                "AAA",
                True,
                "",
                0.20,
            ),
        ),
    )
    output = tmp_path / "persistence"
    script = Path(__file__).parents[1] / "run_candidate_persistence.py"

    # When: the executable CLI is driven through its public surface.
    completed = subprocess.run(
        (str(script), str(screen), "--output-dir", str(output)),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it exits successfully and persists the complete diagnostic surface.
    assert completed.returncode == 0
    assert (output / "candidate_persistence_candidates.csv").is_file()
    assert (output / "candidate_persistence_transitions.csv").is_file()
    report = (output / "candidate_persistence_report_ko.md").read_text(
        encoding="utf-8"
    )
    assert "수익성 결과가 아니다" in report


def _write_screen(path: Path, rows: tuple[PersistenceFixture, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        for row in rows:
            writer.writerow(
                (
                    row.observed_at.isoformat(),
                    row.exchange,
                    row.symbol,
                    row.selected,
                    row.reason,
                    row.change_pct,
                    10.0,
                    9.99,
                    10.01,
                    20.0,
                    60.0,
                    1_000_000.0,
                    100_000,
                    200_000,
                    0.5,
                )
            )
