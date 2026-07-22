from __future__ import annotations

from pathlib import Path

import pytest

import trading_agent.replay as replay

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "example_intraday.csv"


def test_bounded_loader_reads_current_intraday_example() -> None:
    # Given: the repository's current intraday example and a bounded budget.
    # When: it is loaded through the historical research boundary.
    bars = replay.load_bounded_bars(EXAMPLE, max_rows=10, max_sessions=1)

    # Then: all seven ordered bars are available.
    assert len(bars) == 7
    assert bars[0].symbol == "DEMO"
    assert bars[-1].timestamp.minute == 36


def test_bounded_loader_stops_before_oversized_history_is_processed() -> None:
    # Given: a budget smaller than the source row count.
    # When/Then: the boundary rejects the source instead of truncating it.
    with pytest.raises(replay.BoundedReplaySourceError, match="bounded"):
        _ = replay.load_bounded_bars(EXAMPLE, max_rows=6, max_sessions=1)


def test_bounded_loader_rejects_the_forbidden_full_universe_path(tmp_path: Path) -> None:
    # Given: a source under the explicitly forbidden full-universe directory.
    forbidden = tmp_path / "data" / "regend_us_stocks"
    forbidden.mkdir(parents=True)
    source = forbidden / "minute.csv"
    source.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    # When/Then: it is rejected before rows are parsed.
    with pytest.raises(replay.BoundedReplaySourceError, match="bounded"):
        _ = replay.load_bounded_bars(source, max_rows=10, max_sessions=1)
