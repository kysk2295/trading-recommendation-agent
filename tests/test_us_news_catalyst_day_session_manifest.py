from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.us_news_catalyst_day_session_manifest import (
    UsNewsCatalystDaySessionIdentity,
    UsNewsCatalystDaySessionPaths,
    build_us_news_catalyst_day_session_manifest,
    load_us_news_catalyst_day_session_manifest,
    write_us_news_catalyst_day_session_manifest,
)


def test_day_session_manifest_is_immutable_private_and_replayable(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    first = build_us_news_catalyst_day_session_manifest(identity)
    second = build_us_news_catalyst_day_session_manifest(identity)
    path = tmp_path / "session.json"

    assert first == second
    assert write_us_news_catalyst_day_session_manifest(path, first) is True
    assert write_us_news_catalyst_day_session_manifest(path, first) is False
    assert load_us_news_catalyst_day_session_manifest(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_day_session_manifest_rejects_relative_or_mutated_identity(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    manifest = build_us_news_catalyst_day_session_manifest(identity)

    with pytest.raises(ValidationError):
        _ = UsNewsCatalystDaySessionPaths.model_validate(
            {**identity.paths.model_dump(), "projection_root": Path("relative")}
        )
    with pytest.raises(ValidationError):
        _ = type(manifest).model_validate(
            {**manifest.model_dump(), "code_version": "different"}
        )


def _identity(tmp_path: Path) -> UsNewsCatalystDaySessionIdentity:
    root = tmp_path.absolute()
    names = (
        "ledger.sqlite3",
        "research.json",
        "projections",
        "evidence",
        "security.sqlite3",
        "artifacts",
        "plans",
        "profiles",
        "runtime",
        "canonical",
        "features",
        "receipts",
        "reviews",
        "audit.sqlite3",
        "reports",
        "alpaca.env",
    )
    values = tuple(root / name for name in names)
    return UsNewsCatalystDaySessionIdentity(
        strategy_version="us-news-catalyst-recency-v1-code-fixture",
        code_version="fixture-v1",
        session_date=dt.date(2026, 7, 21),
        created_at=dt.datetime(2026, 7, 21, 12, tzinfo=dt.UTC),
        paths=UsNewsCatalystDaySessionPaths(
            experiment_ledger=values[0],
            registration_manifest=values[1],
            projection_root=values[2],
            evidence_root=values[3],
            security_master_store=values[4],
            artifact_root=values[5],
            plan_root=values[6],
            profile_root=values[7],
            runtime_root=values[8],
            canonical_root=values[9],
            feature_root=values[10],
            receipt_root=values[11],
            review_root=values[12],
            audit_store=values[13],
            output_root=values[14],
            secret_path=values[15],
        ),
    )
