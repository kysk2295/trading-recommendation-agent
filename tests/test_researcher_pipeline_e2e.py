from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.experiment_ledger_keys import experiment_trial_event_key, research_hypothesis_card_key
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.intraday_research_loop import (
    IntradayResearchLoopPaths,
    run_intraday_research_loop,
)
from trading_agent.intraday_research_loop_models import IntradayResearchManifest, IntradayReviewerDecision
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    ResearcherContextInput,
    StructuredHypothesisGenerator,
)
from trading_agent.researcher_pipeline import (
    AcceptedResearchProposal,
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
    build_researcher_context,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore
from trading_agent.source_driven_hypothesis_queue import (
    HypothesisQueueRoute,
    project_source_driven_hypothesis_queue,
)

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_EXAMPLE = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"
INPUT_CSV = PROJECT / "examples" / "example_intraday.csv"
INPUT_SHA256 = "2a0222a20540d7d07b95130dc6a7414733f75f5210958820fde8021259e96391"
FOUNDATION = PROJECT / "examples" / "data" / "us-vwap-reclaim-historical-fixture-v1.json"
FOUNDATION_SHA256 = "baccd5b6944d239d4467267b98ab24790a78b20fb68f9d337d0cc1465e276e94"
CALLED_AT = dt.datetime(2026, 7, 23, 2, 31, tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class _E2eResult:
    decision: IntradayReviewerDecision
    route: HypothesisQueueRoute
    next_censored_reasons: tuple[str, ...]


def test_fake_researcher_closes_register_queue_loop_review_and_feedback_cycle(tmp_path: Path) -> None:
    # Given: source evidence, a deterministic model response, and the real local research core.
    # When: the complete fake-first narrative executes through the public library surfaces.
    result = _run_full_cycle(tmp_path)

    # Then: deterministic review and censored-session feedback are both observable.
    assert result.decision is IntradayReviewerDecision.HOLD
    assert result.route is HypothesisQueueRoute.INDEPENDENT_REVIEW
    assert result.next_censored_reasons == ("no_current_session_setup",)


def _run_full_cycle(tmp_path: Path) -> _E2eResult:
    source_payload = json.loads(SOURCE_EXAMPLE.read_text(encoding="utf-8"))
    source = ResearcherContextInput.model_validate(
        {
            "schema_version": 1,
            "lane_id": "intraday_momentum",
            "sources": source_payload["research_sources"],
            "regime_context": "regular_session_high_liquidity",
        }
    )
    response = json.dumps(
        {
            "schema_version": 1,
            "hypothesis_id": source_payload["experiment_scope"]["hypothesis_id"],
            "hypothesis": source_payload["hypothesis"],
            "falsification_rule": (
                "Reject after 20 eligible sessions when profit factor is below 0.75 while the matched "
                "baseline profit factor is at least 1.0."
            ),
            "cited_source_ids": source_payload["research_source_ids"],
            "economic_mechanism": source_payload["economic_mechanism"],
            "counterfactual_baseline": source_payload["counterfactual_baseline"],
            "strategy_source": (
                "def create_strategy(context):\n"
                "    class Strategy:\n"
                "        def observe(self, bar, candidate):\n"
                "            return None\n"
                "    return Strategy()\n"
            ),
            "free_parameters": ["minimum_relative_volume"],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    receipts = ResearcherReceiptStore(tmp_path / "receipts")
    pipeline = ResearcherPipeline(
        ResearcherPipelineServices(
            StructuredHypothesisGenerator(
                FixtureLlmProposalClient(response),
                receipts,
                lambda: CALLED_AT,
            ),
            DeterministicHypothesisCritic(max_free_parameters=4),
        ),
        ResearcherPipelineStores(
            ledger,
            receipts,
            GeneratedStrategyArtifactStore(
                tmp_path / "strategies",
                resolve_generated_strategy_runtime(Path(sys.executable)),
            ),
        ),
        ResearcherPipelineArtifacts(tmp_path / "manifests", tmp_path / "queue"),
    )
    accepted = pipeline.run(
        build_researcher_context(source, ExperimentLedgerReader(ledger.path)),
        max_attempts=1,
    )
    assert isinstance(accepted, AcceptedResearchProposal)
    assert (
        accepted.strategy_artifact.source_path.read_text(encoding="utf-8")
        == accepted.proposal.strategy_draft.source_code
    )
    assert (
        accepted.strategy_artifact.artifact.payload.response_sha256
        == accepted.proposal.llm_receipt.response_sha256
    )
    lane_registry = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
    manifest = _research_manifest(accepted)
    loop = run_intraday_research_loop(
        manifest,
        IntradayResearchLoopPaths(
            input_csv=INPUT_CSV,
            lane_registry=lane_registry,
            experiment_ledger=ledger.path,
            artifact_root=tmp_path / "artifacts",
            review_root=tmp_path / "reviews",
            source_queue_artifact=accepted.queue_path,
            data_foundation_manifests=(FOUNDATION,),
        ),
    )
    _append_censored_feedback(ledger, accepted)
    next_context = build_researcher_context(source, ExperimentLedgerReader(ledger.path))
    route = project_source_driven_hypothesis_queue(
        ExperimentLedgerReader(ledger.path)
    ).snapshot.items[0].route
    return _E2eResult(loop.decisions[0], route, next_context.failure_digest.censored_reasons)


def _research_manifest(accepted: AcceptedResearchProposal) -> IntradayResearchManifest:
    return IntradayResearchManifest.model_validate(
        {
            "schema_version": 2,
            "family": "source_backed_intraday_challengers_v2",
            "code_version": "a" * 40,
            "hypotheses": [
                {
                    "strategy": "vwap_reclaim",
                    "hypothesis_id": accepted.proposal.card.hypothesis.hypothesis_id,
                    "strategy_version": "first_vwap_reclaim_source_v2",
                    "queue_card_key": str(research_hypothesis_card_key(accepted.proposal.card)),
                    "data_foundation_sha256": FOUNDATION_SHA256,
                }
            ],
            "source_queue_snapshot_id": accepted.queue_path.stem.removeprefix("source_hypothesis_queue_"),
            "input_sha256": INPUT_SHA256,
            "registered_at": "2026-07-23T02:32:00Z",
            "evaluator_version": "intraday_walk_forward_v1",
            "minimum_training_sessions": 0,
            "max_bars": 10,
            "max_sessions": 1,
            "per_side_fee_bps": 5,
            "per_side_slippage_bps": 15,
            "bootstrap_samples": 200,
            "rss_limit_gib": 9.5,
        }
    )


def _append_censored_feedback(
    ledger: ExperimentLedgerStore,
    accepted: AcceptedResearchProposal,
) -> None:
    trial = ExperimentTrialRegistration(
        trial_id="vwap-reclaim-shadow-censored-v1",
        strategy_version="first_vwap_reclaim_source_v2",
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=accepted.proposal.card.hypothesis.experiment_scope,
        experiment_scope_key=accepted.proposal.card.hypothesis.experiment_scope_key,
        evaluator_version="forward_session_v1",
        data_version="d" * 64,
        feed_entitlement="alpaca_iex_read_only",
        planned_start=dt.date(2026, 7, 24),
        planned_end=dt.date(2026, 7, 24),
        registered_at=dt.datetime(2026, 7, 23, 2, 33, tzinfo=dt.UTC),
        evidence_budget=("maximum_sessions:1",),
    )
    started = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 23, 2, 34, tzinfo=dt.UTC),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )
    censored = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=2,
        event_kind=TrialEventKind.CENSORED,
        occurred_at=dt.datetime(2026, 7, 23, 2, 35, tzinfo=dt.UTC),
        artifact_sha256s=(),
        reason_codes=("no_current_session_setup",),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        assert writer.register_trial(trial)
        assert writer.append_trial_event(started)
        assert writer.append_trial_event(censored)
