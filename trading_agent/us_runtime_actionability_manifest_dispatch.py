from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionPlan,
    build_alpaca_sip_dynamic_subscription_plan,
    dynamic_subscription_request_bytes,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifest,
    build_alpaca_sip_quote_actionability_manifest,
    write_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.trade_signal_outbox_reader import read_trade_signal_publications
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_quote_actionability_rules import base_is_current
from trading_agent.us_subscription_models import SubscriptionPolicyDecision


class UsRuntimeActionabilityManifestDispatchError(ValueError):
    @override
    def __str__(self) -> str:
        return "US runtime actionability manifest dispatch is blocked"


@dataclass(frozen=True, slots=True)
class UsRuntimeActionabilityManifestDispatchResult:
    manifests: tuple[AlpacaSipQuoteActionabilityManifest, ...]
    created_count: int
    replay_count: int


def dispatch_us_runtime_actionability_manifests(
    publications: tuple[TradeSignalPublication, ...],
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    plan: AlpacaSipDynamicSubscriptionPlan,
    output_root: Path,
) -> UsRuntimeActionabilityManifestDispatchResult:
    try:
        _ = dynamic_subscription_request_bytes(plan)
        if (
            type(publications) is not tuple
            or any(type(item) is not TradeSignalPublication for item in publications)
            or type(bindings) is not tuple
            or any(type(item) is not UsFeatureEvidenceBinding for item in bindings)
        ):
            raise UsRuntimeActionabilityManifestDispatchError
        plan_by_instrument = {item.instrument_id: item.symbol for item in plan.bindings}
        instruments = tuple(item.snapshot.instrument_id for item in bindings)
        if len(instruments) != len(set(instruments)):
            raise UsRuntimeActionabilityManifestDispatchError
        manifests: list[AlpacaSipQuoteActionabilityManifest] = []
        for binding in bindings:
            expected_symbol = plan_by_instrument.get(binding.snapshot.instrument_id)
            if expected_symbol is None or expected_symbol != binding.symbol:
                raise UsRuntimeActionabilityManifestDispatchError
            eligible = tuple(
                publication
                for publication in publications
                if publication.signal.symbol == binding.symbol
                and base_is_current(
                    publication,
                    scan_started_at=publication.signal.observed_at,
                    evaluated_at=binding.snapshot.observed_at,
                )
            )
            if len(eligible) > 1:
                raise UsRuntimeActionabilityManifestDispatchError
            if eligible:
                manifests.append(
                    build_alpaca_sip_quote_actionability_manifest(
                        eligible[0],
                        binding.snapshot,
                        plan,
                        scan_started_at=eligible[0].signal.observed_at,
                    )
                )
        prepared = tuple(manifests)
        created = tuple(
            write_alpaca_sip_quote_actionability_manifest(
                _manifest_path(output_root, manifest),
                manifest,
            )
            for manifest in prepared
        )
        created_count = sum(created)
        return UsRuntimeActionabilityManifestDispatchResult(
            prepared,
            created_count,
            len(created) - created_count,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise UsRuntimeActionabilityManifestDispatchError from None


def dispatch_us_runtime_actionability_outbox(
    signal_outbox: Path,
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    decision: SubscriptionPolicyDecision,
    output_root: Path,
) -> UsRuntimeActionabilityManifestDispatchResult:
    try:
        return dispatch_us_runtime_actionability_manifests(
            read_trade_signal_publications(signal_outbox),
            bindings,
            build_alpaca_sip_dynamic_subscription_plan(decision),
            output_root,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise UsRuntimeActionabilityManifestDispatchError from None


def dispatch_us_runtime_actionability_outbox_counts(
    signal_outbox: Path | None,
    output_root: Path | None,
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    decision: SubscriptionPolicyDecision,
) -> tuple[int, int] | None:
    if signal_outbox is None and output_root is None:
        return None
    if signal_outbox is None or output_root is None:
        raise UsRuntimeActionabilityManifestDispatchError
    result = dispatch_us_runtime_actionability_outbox(
        signal_outbox,
        bindings,
        decision,
        output_root,
    )
    return result.created_count, result.replay_count


def _manifest_path(
    output_root: Path,
    manifest: AlpacaSipQuoteActionabilityManifest,
) -> Path:
    digest = manifest.manifest_id.rpartition(":")[2]
    return output_root.expanduser().absolute() / f"{digest}.json"


__all__ = (
    "UsRuntimeActionabilityManifestDispatchError",
    "UsRuntimeActionabilityManifestDispatchResult",
    "dispatch_us_runtime_actionability_manifests",
    "dispatch_us_runtime_actionability_outbox",
    "dispatch_us_runtime_actionability_outbox_counts",
)
