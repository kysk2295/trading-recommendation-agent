from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.dashboard_options_workbench_models import (
    OptionChainViewV2,
    OptionsWorkbenchV2,
    WorkbenchSectionV2,
)


@dataclass(frozen=True, slots=True)
class InvalidOptionsWorkbenchProjectionError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def project_options_workbench(*, now: dt.datetime, derivatives_trace_id: str) -> OptionsWorkbenchV2:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidOptionsWorkbenchProjectionError(reason="projection_time_not_aware")
    return OptionsWorkbenchV2(
        schema_version=1,
        selected_view="market_pulse",
        market=WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            blocker_code="canonical_option_market_missing",
            summary="통합 옵션 시장 snapshot이 아직 연결되지 않았습니다",
            trace_id=derivatives_trace_id,
        ),
        chain=OptionChainViewV2(
            state="unavailable",
            observed_at=None,
            blocker_code="canonical_option_chain_missing",
            summary="통합 옵션 체인이 아직 연결되지 않았습니다",
            trace_id=derivatives_trace_id,
            underlying=None,
            selected_expiration=None,
            expirations=(),
            total_count=0,
            projected_count=0,
            truncated=False,
            rows=(),
        ),
        scenario=None,
        agent=WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            blocker_code="derivatives_agent_receipt_missing",
            summary="파생상품 Researcher 도구 receipt가 아직 연결되지 않았습니다",
            trace_id=derivatives_trace_id,
        ),
        experiment=WorkbenchSectionV2(
            state="unavailable",
            observed_at=None,
            blocker_code="options_experiment_missing",
            summary="옵션 실험 chain이 아직 연결되지 않았습니다",
            trace_id=derivatives_trace_id,
        ),
        promotions=(),
    )


__all__ = ("InvalidOptionsWorkbenchProjectionError", "project_options_workbench")
