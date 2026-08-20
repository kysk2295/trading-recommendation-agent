from __future__ import annotations

import datetime as dt
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

from tests.day_strategy_capsule_support import bar, builtin_capsule, proposal
from tests.strategy_research_contract_fixtures import hypothesis
from tests.test_day_research_attempt_binding import _attempt, _binding, _family, _version
from trading_agent.day_learning_policy import (
    ExplorationPolicy,
    ExplorationPolicyAction,
    ExplorationPolicyPayload,
)
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
    publish_day_strategy_capsule,
)
from trading_agent.day_strategy_capsule_models import CapsuleArtifactKind, StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_protocol import BarFrame, CandidateFrame
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import EvidenceRef, QuoteValidation
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_types import AttemptStatus
from trading_agent.us_forward_shadow_artifacts import UsForwardShadowArtifactStore
from trading_agent.us_forward_shadow_models import UsForwardShadowTick, completed_bar_id
from trading_agent.us_forward_shadow_runtime import UsForwardShadowServices

SESSION_DATE = dt.date(2026, 8, 20)
CALENDAR_ID = "calendar://official/XNYS/2026-v1"


def prepared_runtime(
    root: Path,
    *,
    source: str,
) -> tuple[UsForwardShadowServices, StrategyCapsule]:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    generated_store = GeneratedStrategyArtifactStore(root / "generated", runtime)
    published = generated_store.publish(proposal(source))
    source_sha256 = published.artifact.payload.source_sha256
    family = _family()
    base_version = _version(family, code_sha256=source_sha256)
    version_payload = base_version.model_dump(mode="python") | {
        "hypothesis_version_id": "",
        "protocol_sha256": generated_protocol_bundle_sha256(),
    }
    version = base_version.model_validate(
        version_payload
        | {"hypothesis_version_id": base_version.canonical_id_for(version_payload)}
    )
    registered_hypothesis = hypothesis().model_copy(update={"code_sha256": source_sha256})
    manifest = PreregistrationManifest.from_hypothesis(
        registered_hypothesis,
        preregistered_at=registered_hypothesis.created_at,
    )
    attempt = _attempt(0, AttemptStatus.SUCCEEDED).model_copy(
        update={
            "hypothesis_id": registered_hypothesis.hypothesis_id,
            "code_sha256": source_sha256,
            "artifact_refs": (f"artifact://safe/{source_sha256}",),
        }
    )
    binding = _binding(attempt, version, artifact_ref=f"artifact://safe/{source_sha256}")
    ledger = ExperimentLedgerStore(root / "ledger.sqlite3")
    with ledger.writer() as writer:
        assert writer.register_strategy_research(manifest)
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    limits = builtin_capsule().resource_limits
    sandbox = GeneratedStrategySandbox(runtime, root / "preflight", limits.to_generated_limits())
    request = DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        market_id=MarketId.US_EQUITIES,
        artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
        artifact_ref=binding.artifact_ref,
        artifact_sha256=source_sha256,
        generated_artifact_id=published.artifact.artifact_id,
        evaluation_cadence=version.evaluation_cadence,
        evidence_schema=("completed_bar_v1",),
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        target_rule="host_projects_preregistered_targets",
        cost_model=version.cost_model,
        slippage_model_id="bounded_intraday_slippage_v1",
        resource_limits=limits,
        risk_policy_ref="risk-policy://day-research/v1",
        protocol_version=1,
        protocol_sha256=generated_protocol_bundle_sha256(),
        evaluator_sha256=generated_evaluator_bundle_sha256(),
        published_at=binding.bound_at + dt.timedelta(minutes=1),
        authority_ceiling=builtin_capsule().authority_ceiling,
        generated_verification=GeneratedCapsuleVerification(generated_store, sandbox, (bar(),)),
    )
    capsule, created = publish_day_strategy_capsule(ledger, request)
    assert created
    policy_payload = ExplorationPolicyPayload(
        final_report_id="f" * 64,
        market_id=MarketId.US_EQUITIES,
        action=ExplorationPolicyAction.KEEP,
        calendar_snapshot_id=CALENDAR_ID,
        effective_session_date=SESSION_DATE,
        effective_at=dt.datetime(2026, 8, 20, 13, 30, tzinfo=dt.UTC),
        active_capsule_ids=(capsule.capsule_id,),
        queued_capsule_ids=(),
        feedback_decision_ids=(),
        policy_version="day-exploration-policy-v1",
    )
    policy = ExplorationPolicy(
        policy_id=hashlib.sha256(canonical_experiment_ledger_json(policy_payload).encode()).hexdigest(),
        payload=policy_payload,
    )
    with ledger.writer() as writer:
        assert writer.record_day_exploration_policy(policy)
    return (
        UsForwardShadowServices(
            ledger=ledger,
            generated_artifacts=generated_store,
            shadow_artifacts=UsForwardShadowArtifactStore(root / "shadow"),
            task_root=root / "runtime-tasks",
        ),
        capsule,
    )


def shadow_tick(
    services: UsForwardShadowServices,
    minute: int,
    sequence: int,
    *,
    low: float | None = None,
    high: float | None = None,
    policy_id: str | None = None,
) -> UsForwardShadowTick:
    bars = tuple(_bar_frame(index, low=low if index == minute else None, high=high if index == minute else None)
                 for index in range(max(0, minute - 2), minute + 1))
    latest = bars[-1]
    observed = latest.timestamp + dt.timedelta(seconds=30)
    stored_policy = services.ledger.reader().day_exploration_policies(MarketId.US_EQUITIES)[0]
    return UsForwardShadowTick(
        market_id=MarketId.US_EQUITIES,
        policy_id=stored_policy.policy_id if policy_id is None else policy_id,
        session_id="XNYS-2026-08-20",
        session_date=SESSION_DATE,
        calendar_snapshot_id=CALENDAR_ID,
        completed_bar_id=completed_bar_id(latest),
        completed_bar_sequence=sequence,
        bars=bars,
        candidate=CandidateFrame(
            symbol="AAPL",
            timestamp=latest.timestamp,
            price=latest.close,
            gap_pct=1.0,
            change_pct=1.0,
            relative_volume=2.0,
            cumulative_dollar_volume=1_000_000.0,
            spread_bps=9.9,
            catalyst="earnings",
        ),
        quote=QuoteValidation(
            bid=Decimal(str(latest.close - 0.05)),
            ask=Decimal(str(latest.close + 0.05)),
            observed_at=latest.timestamp + dt.timedelta(seconds=25),
            valid_until=latest.timestamp + dt.timedelta(seconds=45),
            spread_bps=Decimal(str(0.1 / latest.close * 10_000)),
            max_slippage_bps=Decimal("20"),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="research/current_bar",
                record_id=completed_bar_id(latest),
                observed_at=latest.timestamp,
            ),
        ),
        observed_at=observed,
    )


def signal_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            if bar['symbol'] != 'AAPL':\n"
        "                return None\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': bar['close'], 'stop': bar['close'] - 1.0, "
        "'rationale': 'research-only breakout'}\n"
        "    return Strategy()\n"
    )


def no_signal_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return None\n"
        "    return Strategy()\n"
    )


def failing_source() -> str:
    return (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            if bar['symbol'] == 'AAPL':\n"
        "                raise RuntimeError('fixture failure')\n"
        "            return None\n"
        "    return Strategy()\n"
    )


def _bar_frame(minute: int, *, low: float | None, high: float | None) -> BarFrame:
    close = 101.0
    return BarFrame(
        symbol="AAPL",
        timestamp=dt.datetime(2026, 8, 20, 14, minute, tzinfo=dt.UTC),
        open=100.8,
        high=101.3 if high is None else high,
        low=100.5 if low is None else low,
        close=close,
        volume=10_000,
        prior_close=99.0,
        average_daily_volume=1_000_000,
        spread_bps=10.0,
        catalyst="earnings",
    )
