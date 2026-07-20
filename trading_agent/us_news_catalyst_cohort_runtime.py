from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_runtime_adapter import normalize_alpaca_sip_runtime_bars
from trading_agent.alpaca_sip_runtime_evidence import AlpacaSipRuntimeEvidenceProjector
from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_models import AlpacaSipMinutePageRequest
from trading_agent.intraday_feature_kernel import build_intraday_feature_snapshot
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_intraday_volume_profile_models import IntradayVolumeProfileEvidence
from trading_agent.us_news_catalyst_feature_models import UsNewsCatalystFeatureArtifact
from trading_agent.us_news_catalyst_feature_projection import (
    project_us_news_catalyst_feature_artifact,
)
from trading_agent.us_subscription_models import DesiredMarketDataSubscription


class InvalidUsNewsCatalystCohortRuntimeError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst cohort runtime collection is blocked"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystCohortRuntimePaths:
    runtime_root: Path
    canonical_root: Path


@dataclass(frozen=True, slots=True)
class UsNewsCatalystCohortFeatureRequest:
    subscription: DesiredMarketDataSubscription
    profile: IntradayVolumeProfileEvidence
    session_date: dt.date
    evaluated_at: dt.datetime
    completed_minute: int


def collect_us_news_catalyst_cohort_feature(
    page_client: AlpacaSipMinutePageClient,
    paths: UsNewsCatalystCohortRuntimePaths,
    request: UsNewsCatalystCohortFeatureRequest,
) -> UsNewsCatalystFeatureArtifact:
    try:
        _validate_request(page_client, paths, request)
        bounds = regular_session_bounds(request.session_date)
        if bounds is None:
            raise InvalidUsNewsCatalystCohortRuntimeError
        opened_at, _closed_at = bounds
        boundary = opened_at + dt.timedelta(minutes=request.completed_minute)
        page_request = AlpacaSipMinutePageRequest(
            request.session_date,
            request.subscription.symbol,
            opened_at,
            boundary - dt.timedelta(microseconds=1),
        )
        owner_key = _owner_key(request.subscription)
        owner = _private_directory(_private_directory(paths.runtime_root) / owner_key)
        evidence = AlpacaSipRuntimeEvidenceStore(owner / "evidence.sqlite3")
        page_set = evidence.load_page_set(page_request)
        if page_set is None:
            page_set = page_client.fetch_page(page_request)
            for page in page_set.pages:
                _ = evidence.append_page(page_request, page)
        if any(page.received_at != request.evaluated_at for page in page_set.pages):
            raise InvalidUsNewsCatalystCohortRuntimeError
        bars = normalize_alpaca_sip_runtime_bars(page_set, opened_at, boundary)
        if tuple(item.sequence for item in bars) != tuple(
            range(1, request.completed_minute + 1)
        ):
            raise InvalidUsNewsCatalystCohortRuntimeError
        identity = AlpacaSipRuntimeEvidenceProjector(
            evidence,
            _private_directory(paths.canonical_root) / owner_key,
        ).project(page_set, request.subscription.instrument_id, bars)
        snapshot = build_intraday_feature_snapshot(
            identity,
            request.subscription.instrument_id,
            request.evaluated_at,
            tuple(item.completed_bar for item in bars),
            request.profile,
        )
        return project_us_news_catalyst_feature_artifact(
            UsFeatureEvidenceBinding(request.subscription.symbol, snapshot)
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise InvalidUsNewsCatalystCohortRuntimeError from None


def _validate_request(
    page_client: AlpacaSipMinutePageClient,
    paths: UsNewsCatalystCohortRuntimePaths,
    request: UsNewsCatalystCohortFeatureRequest,
) -> None:
    if (
        type(page_client) is not AlpacaSipMinutePageClient
        or type(paths) is not UsNewsCatalystCohortRuntimePaths
        or type(request) is not UsNewsCatalystCohortFeatureRequest
        or type(request.subscription) is not DesiredMarketDataSubscription
        or type(request.profile) is not IntradayVolumeProfileEvidence
        or request.profile.instrument_id != request.subscription.instrument_id
        or request.profile.target_session_date != request.session_date
        or request.profile.through_minute != request.completed_minute
        or type(request.evaluated_at) is not dt.datetime
        or request.evaluated_at.tzinfo is None
        or request.evaluated_at.utcoffset() is None
    ):
        raise InvalidUsNewsCatalystCohortRuntimeError


def _owner_key(subscription: DesiredMarketDataSubscription) -> str:
    payload = {
        "instrument_id": subscription.instrument_id,
        "symbol": subscription.symbol,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _private_directory(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    try:
        if candidate.is_symlink():
            raise InvalidUsNewsCatalystCohortRuntimeError
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = candidate.lstat()
    except OSError:
        raise InvalidUsNewsCatalystCohortRuntimeError from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InvalidUsNewsCatalystCohortRuntimeError
    return candidate


__all__ = (
    "InvalidUsNewsCatalystCohortRuntimeError",
    "UsNewsCatalystCohortFeatureRequest",
    "UsNewsCatalystCohortRuntimePaths",
    "collect_us_news_catalyst_cohort_feature",
)
