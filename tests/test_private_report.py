from __future__ import annotations

from pathlib import Path

import run_alpaca_paper_bootstrap as bootstrap
import run_alpaca_paper_entry_smoke as entry_smoke
import run_alpaca_paper_mutation_recovery as mutation_recovery
import run_alpaca_paper_preflight as preflight
import run_alpaca_paper_protective_oco_smoke as protective_oco_smoke
import run_alpaca_paper_readiness as readiness
import run_alpaca_paper_recovery as recovery
import run_alpaca_paper_safety as safety
import run_alpaca_paper_safety_mutation_smoke as safety_smoke
from trading_agent.paper_safety_models import BlockedPaperSafetyPlan
from trading_agent.private_report import write_private_report


def test_private_report_creates_parent_and_forces_mode_600(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "report.md"

    write_private_report(destination, "first\n")

    assert destination.read_text(encoding="utf-8") == "first\n"
    assert destination.stat().st_mode & 0o777 == 0o600


def test_private_report_replaces_an_existing_world_readable_file(tmp_path: Path) -> None:
    destination = tmp_path / "report.md"
    destination.write_text("stale\n", encoding="utf-8")
    destination.chmod(0o644)

    write_private_report(destination, "current\n")

    assert destination.read_text(encoding="utf-8") == "current\n"
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not tuple(destination.parent.glob(".report.md.*.writing"))


def test_all_paper_operational_cli_reports_are_private(tmp_path: Path) -> None:
    bootstrap._write_report(tmp_path / "bootstrap", bound=False, reasons=())
    preflight._write_report(
        tmp_path / "preflight",
        preflight.PreflightReport(False, 0, 0, ()),
    )
    readiness._write_report(
        tmp_path / "readiness",
        readiness.RuntimeReadinessReport(None, False, False, False, 0, 0, ()),
    )
    recovery._write_report(tmp_path / "recovery", None, ())
    mutation_recovery._write_report(tmp_path / "mutation-recovery", (), ())
    safety._write_report(tmp_path / "safety", BlockedPaperSafetyPlan(("blocked",)))
    entry_smoke._write_report(tmp_path / "entry", "blocked", ())
    protective_oco_smoke._write_report(tmp_path / "protective", "blocked", ())
    safety_smoke._write_report(tmp_path / "safety-smoke", "blocked", ())

    reports = tuple(tmp_path.rglob("*.md"))
    assert len(reports) == 9
    assert all(report.stat().st_mode & 0o777 == 0o600 for report in reports)
