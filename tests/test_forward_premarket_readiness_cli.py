from __future__ import annotations

import csv
import datetime as dt
import stat
import subprocess
from collections.abc import Iterable
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import typer

import run_forward_premarket_readiness as cli
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.ranking_journal import (
    RANKING_COVERAGE_FIELDS,
    RANKING_FIELDS,
)

NOW = dt.datetime(2026, 7, 23, 9, 25, tzinfo=ZoneInfo("America/New_York"))
REPORT_NAME = "forward_premarket_readiness_ko.md"
ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_forward_premarket_readiness.py"
type CsvCell = str | int | float | bool


def test_pep723_cli_declares_transitive_runtime_dependencies() -> None:
    completed = subprocess.run(
        ("uv", "run", str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_cli_materializes_strict_current_premarket_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = tmp_path / "live_sessions/20260723"
    _write_session(session)
    output = tmp_path / "readiness"
    monkeypatch.setattr(cli, "_clock", lambda: NOW)

    cli.main(
        session_dir=session,
        session_date="2026-07-23",
        minimum_cycles=3,
        maximum_latest_age_seconds=600,
        minimum_latest_selected=1,
        output_dir=output,
    )

    report = output / REPORT_NAME
    content = report.read_text(encoding="utf-8")
    assert "- result: ready" in content
    assert "- premarket cycles: 3" in content
    assert "- ranking requests: 18" in content
    assert "- latest selected candidates: 1" in content
    assert "- quality gate relaxed: false" in content
    assert "- external provider/account/order mutation: 0" in content
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


def test_cli_preserves_failed_ranking_request_as_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = tmp_path / "live_sessions/20260723"
    _write_session(session, failed_request=True)
    output = tmp_path / "readiness"
    monkeypatch.setattr(cli, "_clock", lambda: NOW)

    with pytest.raises(typer.Exit) as captured:
        cli.main(
            session_dir=session,
            session_date="2026-07-23",
            minimum_cycles=3,
            maximum_latest_age_seconds=600,
            minimum_latest_selected=1,
            output_dir=output,
        )

    assert captured.value.exit_code == 1
    content = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert "- result: blocked" in content
    assert "- blocker: ranking_request_failures:1" in content


def _write_session(session: Path, *, failed_request: bool = False) -> None:
    session.mkdir(parents=True)
    watch_rows: list[tuple[str, int, str]] = []
    coverage_rows: list[tuple[str, str, str, str, int, str]] = []
    snapshot_rows: list[tuple[CsvCell, ...]] = []
    risk_rows: list[tuple[CsvCell, ...]] = []
    exchanges = ("NAS", "NYS", "AMS")
    sources = ("updown", "volume")
    for cycle_index, minute in enumerate((10, 15, 20)):
        started = NOW.replace(minute=minute, second=0)
        observed = started + dt.timedelta(seconds=2)
        watch_rows.append((started.isoformat(), 0, "ok"))
        for exchange in exchanges:
            for source in sources:
                failed = failed_request and cycle_index == 2 and exchange == "AMS" and source == "volume"
                symbol = f"{exchange}{source[0].upper()}"
                coverage_rows.append(
                    (
                        observed.isoformat(),
                        source,
                        exchange,
                        "failed" if failed else "ok",
                        0 if failed else 1,
                        "HTTP 503" if failed else "",
                    )
                )
                if failed:
                    continue
                selected = exchange == "NAS" and source == "updown"
                snapshot_rows.append(
                    (
                        observed.isoformat(),
                        source,
                        exchange,
                        1,
                        symbol,
                        symbol,
                        10,
                        0.05,
                        9.99,
                        10.01,
                        20,
                        100_000,
                        1_000_000,
                        500_000,
                        selected,
                        selected,
                    )
                )
        risk_rows.append(
            (
                observed.isoformat(),
                "NAS",
                "NASU",
                True,
                "",
                0.05,
                10,
                9.99,
                10.01,
                20,
                60,
                1_000_000,
                100_000,
                500_000,
                0.2,
            )
        )
    _write_csv(
        session / "premarket_watch_cycles.csv",
        ("started_at", "exit_code", "status"),
        watch_rows,
    )
    _write_csv(
        session / "premarket_ranking_request_coverage.csv",
        RANKING_COVERAGE_FIELDS,
        coverage_rows,
    )
    _write_csv(
        session / "premarket_ranking_snapshots.csv",
        RANKING_FIELDS,
        snapshot_rows,
    )
    _write_csv(
        session / "premarket_risk_screen.csv",
        MARKET_RISK_HEADER,
        risk_rows,
    )


def _write_csv(
    path: Path,
    header: tuple[str, ...],
    rows: Iterable[tuple[CsvCell, ...]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    path.chmod(0o600)
