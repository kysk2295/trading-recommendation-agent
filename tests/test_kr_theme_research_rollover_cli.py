from __future__ import annotations

import datetime as dt
import stat
import subprocess
from pathlib import Path

import run_kr_theme_research_rollover as cli
from tests.test_kr_theme_research_rollover import (
    CODE_VERSION,
    DAY_MANIFEST,
    OPPORTUNITY_MANIFEST,
    POLICY,
    _base_ledger,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_research_rollover.py"


def test_rollover_cli_help_and_private_happy_replay(tmp_path: Path) -> None:
    help_result = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    ledger = _base_ledger(tmp_path)
    output = tmp_path / "rollover"
    args = (
        "--opportunity-manifest",
        str(OPPORTUNITY_MANIFEST),
        "--day-manifest",
        str(DAY_MANIFEST),
        "--policy",
        str(POLICY),
        "--database",
        str(ledger.path),
        "--output-dir",
        str(output),
        "--code-version",
        CODE_VERSION,
    )
    first = cli.main(args, now=dt.datetime(2026, 7, 24, 7, tzinfo=dt.UTC))
    replay = cli.main(
        args,
        now=dt.datetime(2026, 7, 24, 8, tzinfo=dt.UTC),
    )

    assert help_result.returncode == 0
    assert "--opportunity-manifest" in help_result.stdout
    assert first == 0
    assert replay == 0
    report = (output / "kr_theme_research_rollover_ko.md").read_text(
        encoding="utf-8"
    )
    assert "strategy versions 신규/재사용: 0/2" in report
    assert "external mutation: 0" in report
    assert stat.S_IMODE(
        (output / "kr_theme_research_rollover_ko.md").stat().st_mode
    ) == 0o600


def test_rollover_cli_bad_code_leaves_versions_unchanged(tmp_path: Path) -> None:
    ledger = _base_ledger(tmp_path)
    output = tmp_path / "rollover"

    result = cli.main(
        (
            "--opportunity-manifest",
            str(OPPORTUNITY_MANIFEST),
            "--day-manifest",
            str(DAY_MANIFEST),
            "--policy",
            str(POLICY),
            "--database",
            str(ledger.path),
            "--output-dir",
            str(output),
            "--code-version",
            "not-a-commit",
        ),
        now=dt.datetime(2026, 7, 24, 7, tzinfo=dt.UTC),
    )

    assert result == 1
    assert len(ledger.multi_market_strategy_versions()) == 2
    assert "결과: blocked" in (
        output / "kr_theme_research_rollover_ko.md"
    ).read_text(encoding="utf-8")
