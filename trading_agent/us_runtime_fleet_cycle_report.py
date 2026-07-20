from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trading_agent.alpaca_sip_dynamic_plan_store import AlpacaSipDynamicPlanRollResult
from trading_agent.private_report import write_private_report
from trading_agent.us_runtime_actionability_plan import dynamic_plan_report_detail
from trading_agent.us_runtime_live_actionability_config import live_actionability_report_detail
from trading_agent.us_runtime_live_actionability_dispatch import UsRuntimeLiveActionabilityDispatchResult


@dataclass(frozen=True, slots=True)
class RuntimeFleetCycleReportFields:
    ready: bool
    fleet_status: str
    gate_status: str
    owner_count: int
    audit_appended: bool
    state_appended: bool
    plan_roll: AlpacaSipDynamicPlanRollResult | None
    research_counts: tuple[int, int] | None
    news_catalyst_feature_counts: tuple[int, int] | None
    actionability_counts: tuple[int, int] | None
    live_actionability: UsRuntimeLiveActionabilityDispatchResult | None


def write_runtime_fleet_cycle_report(
    output_dir: Path,
    report_name: str,
    details: tuple[str, ...],
) -> None:
    content = "\n".join(
        (
            "# US runtime fleet cycle",
            "",
            "> Scanner/profile 기반 Alpaca SIP GET-only M4.4 결과입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / report_name, content)


def write_runtime_fleet_cycle_ready_report(
    output_dir: Path,
    report_name: str,
    fields: RuntimeFleetCycleReportFields,
) -> None:
    research = _count_detail("research evidence artifact", fields.research_counts)
    news_features = _count_detail(
        "news catalyst feature artifact",
        fields.news_catalyst_feature_counts,
    )
    manifests = _count_detail("actionability manifests", fields.actionability_counts)
    write_runtime_fleet_cycle_report(
        output_dir,
        report_name,
        (
            f"result: {'ready' if fields.ready else 'blocked'}",
            f"fleet: {fields.fleet_status}",
            f"gate: {fields.gate_status}",
            f"owner count: {fields.owner_count}",
            f"audit append: {'new' if fields.audit_appended else 'replay'}",
            f"policy state append: {'new' if fields.state_appended else 'replay'}",
            dynamic_plan_report_detail(fields.plan_roll),
            research,
            news_features,
            manifests,
            live_actionability_report_detail(fields.live_actionability),
            "account/order mutation: 0",
        ),
    )


def _count_detail(name: str, counts: tuple[int, int] | None) -> str:
    if counts is None:
        return f"{name}: disabled"
    return f"{name}: {counts[0]} new, {counts[1]} replay"


__all__ = (
    "RuntimeFleetCycleReportFields",
    "write_runtime_fleet_cycle_ready_report",
    "write_runtime_fleet_cycle_report",
)
