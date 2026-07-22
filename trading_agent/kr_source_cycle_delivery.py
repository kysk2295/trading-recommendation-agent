from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final, override
from zoneinfo import ZoneInfo

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeStore,
    UnsupportedKrThemeSchemaError,
)

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EXPECTED_SOURCES: Final = tuple(sorted(KrCatalystSource, key=lambda item: item.value))
_KST: Final = ZoneInfo("Asia/Seoul")


class InvalidKrSourceCycleDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR source cycle delivery projection is invalid"


@dataclass(frozen=True, slots=True)
class KrSourceCycleDeliveryRequest:
    collection_cycle_id: str
    projected_at: dt.datetime


def project_kr_source_cycle_incident(
    source_store: KrThemeStore,
    delivery_store: HermesDeliveryStore,
    request: KrSourceCycleDeliveryRequest,
) -> HermesProjectionResult:
    try:
        runs = source_store.source_runs(request.collection_cycle_id)
        collection_dates = {run.collection_date for run in runs}
        if (
            _SAFE_ID.fullmatch(request.collection_cycle_id) is None
            or not runs
            or any(run.collection_cycle_id != request.collection_cycle_id for run in runs)
            or len(collection_dates) != 1
            or None in collection_dates
            or request.projected_at.tzinfo is None
            or request.projected_at.utcoffset() is None
            or request.projected_at < max(run.completed_at for run in runs)
            or request.projected_at.astimezone(_KST).date() not in collection_dates
        ):
            raise InvalidKrSourceCycleDeliveryError
        present = frozenset(run.source for run in runs)
        missing = tuple(source for source in _EXPECTED_SOURCES if source not in present)
        failed = tuple(run for run in runs if run.status is KrCoverageStatus.FAILED)
        if not missing and not failed:
            raise InvalidKrSourceCycleDeliveryError
        material = (
            request.collection_cycle_id,
            tuple(
                (run.source.value, run.status.value, run.failure_code)
                for run in sorted(runs, key=lambda item: item.source.value)
            ),
            tuple(source.value for source in missing),
        )
        encoded = json.dumps(material, ensure_ascii=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        missing_text = ", ".join(source.value for source in missing) or "없음"
        failed_text = ", ".join(run.source.value for run in failed) or "없음"
        record = HermesProjectionRecord(
            source_event_id=f"kr-source-cycle-incident-{digest}",
            root_source_event_id=None,
            kind=HermesDeliveryKind.INCIDENT,
            market_id="kr_equities",
            agent_family="opportunity_manager",
            lane_id=None,
            strategy_version=None,
            instrument_id=None,
            occurred_at=max(run.completed_at for run in runs),
            status="blocked_source_incomplete",
            evidence_refs=tuple(sorted(f"kr-source-run:{run.source_run_id}" for run in runs)),
            rendered_text=(
                "KR Opportunity Manager: 추천 차단. "
                f"source coverage {len(runs)}/{len(_EXPECTED_SOURCES)}, "
                f"누락 {missing_text}, 실패 {failed_text}."
            ),
            payload_sha256=digest,
        )
        with delivery_store.writer() as writer:
            return project_outcomes((record,), writer)
    except InvalidKrSourceCycleDeliveryError:
        raise
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        InvalidKrThemeSourceError,
        UnsupportedKrThemeSchemaError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidKrSourceCycleDeliveryError from None


__all__ = (
    "InvalidKrSourceCycleDeliveryError",
    "KrSourceCycleDeliveryRequest",
    "project_kr_source_cycle_incident",
)
