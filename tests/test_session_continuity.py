from __future__ import annotations

import csv
import datetime as dt
import subprocess
from dataclasses import dataclass
from pathlib import Path

from trading_agent import session_continuity
from trading_agent.market_risk import MARKET_RISK_HEADER, PORTFOLIO_LIMIT_REASON


@dataclass(frozen=True, slots=True)
class RiskFixture:
    exchange: str
    symbol: str
    selected: bool
    reason: str
    change_pct: float


def test_continuity_maps_daytime_venues_and_preserves_rejected_candidates(
    tmp_path: Path,
) -> None:
    # Given: full risk populations across daytime, premarket, and regular phases.
    daytime = tmp_path / "daytime.csv"
    premarket = tmp_path / "premarket.csv"
    regular = tmp_path / "regular.csv"
    _write_screen(
        daytime,
        (
            RiskFixture("BAQ", "AAA", True, "", 0.20),
            RiskFixture("BAQ", "BBB", False, PORTFOLIO_LIMIT_REASON, 0.10),
            RiskFixture("BAQ", "WIDE", False, "스프레드 초과", 0.30),
        ),
    )
    _write_screen(
        premarket,
        (
            RiskFixture("NAS", "AAA", True, "", 0.18),
            RiskFixture("NAS", "BBB", True, "", 0.12),
            RiskFixture("NAS", "WIDE", True, "", 0.25),
        ),
    )
    _write_screen(
        regular,
        (RiskFixture("NAS", "AAA", False, PORTFOLIO_LIMIT_REASON, 0.15),),
    )

    # When: the full populations are compared by canonical venue and symbol.
    result = session_continuity.analyze_session_continuity(
        session_continuity.SessionFiles(daytime, premarket, regular)
    )

    # Then: portfolio-limit rows stay eligible and true risk rejects do not continue.
    by_symbol = {candidate.symbol: candidate for candidate in result.candidates}
    assert tuple(by_symbol) == ("AAA", "BBB", "WIDE")
    assert by_symbol["AAA"].canonical_exchange == "NAS"
    assert by_symbol["AAA"].daytime_to_premarket
    assert by_symbol["AAA"].premarket_to_regular
    assert by_symbol["BBB"].daytime_to_premarket
    assert not by_symbol["BBB"].premarket_to_regular
    assert not by_symbol["WIDE"].daytime_to_premarket
    summaries = {
        (summary.source_phase.value, summary.destination_phase.value): summary
        for summary in result.summaries
    }
    assert summaries[("daytime", "premarket")].source_eligible_count == 2
    assert summaries[("daytime", "premarket")].continued_count == 2
    assert summaries[("premarket", "regular")].continuation_rate == 1 / 3
    assert summaries[("daytime", "regular")].continuation_rate == 0.5


def test_continuity_outputs_keep_missing_phases_blank(tmp_path: Path) -> None:
    # Given: one daytime-only candidate and no future phase observations.
    daytime = tmp_path / "daytime.csv"
    _write_screen(daytime, (RiskFixture("BAQ", "AAA", True, "", 0.20),))
    result = session_continuity.analyze_session_continuity(
        session_continuity.SessionFiles(daytime, None, None)
    )
    output = tmp_path / "analysis"

    # When: the diagnostic result is persisted.
    session_continuity.write_continuity_outputs(output, result)

    # Then: absent future observations are not converted into zero-return results.
    with (output / "session_continuity_candidates.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        candidate = next(iter(csv.DictReader(handle)))
    with (output / "session_continuity_summary.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        summaries = tuple(csv.DictReader(handle))
    report = (output / "session_continuity_report_ko.md").read_text(encoding="utf-8")
    assert candidate["premarket_first_observed_at"] == ""
    assert candidate["regular_first_observed_at"] == ""
    assert candidate["premarket_risk_eligible"] == ""
    assert candidate["premarket_selected"] == ""
    assert summaries[0]["continuation_rate"] == ""
    assert "수익성 결과가 아니다" in report


def test_session_continuity_cli_writes_the_diagnostic_surface(tmp_path: Path) -> None:
    # Given: a real session directory with one daytime risk population.
    _write_screen(
        tmp_path / "daytime_risk_screen.csv",
        (RiskFixture("BAQ", "AAA", True, "", 0.20),),
    )
    output = tmp_path / "continuity"
    script = Path(__file__).parents[1] / "run_session_continuity.py"

    # When: the executable CLI analyzes the session.
    completed = subprocess.run(
        (str(script), str(tmp_path), "--output-dir", str(output)),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it exits successfully and creates all three diagnostic artifacts.
    assert completed.returncode == 0
    assert (output / "session_continuity_candidates.csv").is_file()
    assert (output / "session_continuity_summary.csv").is_file()
    assert (output / "session_continuity_report_ko.md").is_file()


def _write_screen(path: Path, rows: tuple[RiskFixture, ...]) -> None:
    observed_at = dt.datetime(2026, 7, 13, 3, 0, tzinfo=dt.UTC)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        for row in rows:
            writer.writerow(
                (
                    observed_at.isoformat(),
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
