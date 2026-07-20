from __future__ import annotations

import datetime as dt
import stat
import subprocess
from pathlib import Path

from run_us_news_catalyst_setup_observation import REPORT_NAME, main
from tests.test_us_news_catalyst_feature_observations import (
    SETUP_AT,
    SYMBOLS,
    _binding,
    _cohort,
)
from trading_agent.us_news_catalyst_feature_artifact import (
    publish_us_news_catalyst_feature_artifact,
)
from trading_agent.us_news_catalyst_feature_projection import (
    project_us_news_catalyst_feature_artifact,
)
from trading_agent.us_news_catalyst_trial_artifact import (
    publish_us_news_catalyst_cohort,
    setup_manifests_in,
)

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_us_news_catalyst_setup_observation.py"


def test_setup_observation_cli_help_is_executable() -> None:
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT), "--help"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--feature-root" in result.stdout


def test_setup_observation_cli_publishes_private_manifest_and_replays(tmp_path: Path) -> None:
    cohort_root = tmp_path / "cohort"
    feature_root = tmp_path / "features"
    artifact_root = tmp_path / "observations"
    report_root = tmp_path / "report"
    cohort_path, _created = publish_us_news_catalyst_cohort(cohort_root, _cohort(tmp_path))
    for index, symbol in enumerate(SYMBOLS, start=1):
        artifact = project_us_news_catalyst_feature_artifact(_binding(symbol, index))
        _ = publish_us_news_catalyst_feature_artifact(feature_root, artifact)
    argv = [
        "--cohort",
        str(cohort_path),
        "--feature-root",
        str(feature_root),
        "--artifact-root",
        str(artifact_root),
        "--output-dir",
        str(report_root),
    ]
    def clock() -> dt.datetime:
        return SETUP_AT + dt.timedelta(seconds=30)

    assert main(argv, clock=clock) == 0
    assert main(argv, clock=clock) == 0

    manifests = setup_manifests_in(artifact_root)
    assert len(manifests) == 1
    assert tuple(item.symbol for item in manifests[0].observations) == SYMBOLS
    assert stat.S_IMODE(next(artifact_root.glob("*.json")).stat().st_mode) == 0o600
    report = (report_root / REPORT_NAME).read_text(encoding="utf-8")
    assert "result: ready" in report
    assert "observation artifact: replay" in report
    assert "provider request: 0" in report
    assert "order mutation: 0" in report


def test_setup_observation_cli_bad_input_is_redacted_and_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "secret-looking-cohort-name.json"
    report_root = tmp_path / "report"

    code = main(
        [
            "--cohort",
            str(missing),
            "--feature-root",
            str(tmp_path / "features"),
            "--artifact-root",
            str(tmp_path / "observations"),
            "--output-dir",
            str(report_root),
        ]
    )

    assert code == 1
    report = (report_root / REPORT_NAME).read_text(encoding="utf-8")
    assert "result: blocked" in report
    assert "observation artifact: not-published" in report
    assert missing.name not in report
    assert "order mutation: 0" in report
