#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///
# --- How to run ---
# uv run python run_us_news_catalyst_setup_observation.py --help

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_news_catalyst_feature_artifact import feature_artifacts_in
from trading_agent.us_news_catalyst_feature_models import (
    InvalidUsNewsCatalystFeatureModelError,
)
from trading_agent.us_news_catalyst_feature_projection import (
    InvalidUsNewsCatalystFeatureProjectionError,
    project_us_news_catalyst_setup_observations,
)
from trading_agent.us_news_catalyst_trial_artifact import (
    load_us_news_catalyst_cohort,
    publish_us_news_catalyst_setup_observation_manifest,
)
from trading_agent.us_news_catalyst_trial_models import InvalidUsNewsCatalystTrialModelError

REPORT_NAME = "us_news_catalyst_setup_observation_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="동일 cycle 피처로 US news-catalyst setup observation 생성"
    )
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    try:
        manifest = project_us_news_catalyst_setup_observations(
            load_us_news_catalyst_cohort(args.cohort),
            feature_artifacts_in(args.feature_root),
            evaluated_at=clock(),
        )
        _path, created = publish_us_news_catalyst_setup_observation_manifest(
            args.artifact_root,
            manifest,
        )
        _write_report(args.output_dir, ready=True, created=created)
        return 0
    except (
        InvalidUsNewsCatalystFeatureModelError,
        InvalidUsNewsCatalystFeatureProjectionError,
        InvalidUsNewsCatalystTrialModelError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, ready=False, created=False)
        return 1


def _write_report(output_dir: Path, *, ready: bool, created: bool) -> None:
    result = "ready" if ready else "blocked"
    artifact_status = ("new" if created else "replay") if ready else "not-published"
    lines = (
        "# US news-catalyst setup observation",
        "",
        "> local immutable research evidence only; no provider, account, or order authority.",
        "",
        f"- result: {result}",
        f"- observation artifact: {artifact_status}",
        "- provider request: 0",
        "- credential read: 0",
        "- account read: 0",
        "- order mutation: 0",
        "",
    )
    write_private_stable_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
