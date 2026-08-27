from __future__ import annotations

import datetime as dt
from contextlib import ExitStack
from dataclasses import dataclass

from trading_agent.autonomous_browser_tools import BrowserToolServices
from trading_agent.autonomous_supervisor_service import build_autonomous_supervisor
from trading_agent.browser_research_agenda import ContinuousBrowserResearchSupervisor
from trading_agent.browser_social_evidence_store import (
    BrowserSocialEvidenceStore,
    InvalidBrowserSocialEvidenceStoreError,
)
from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.day_discovery_loop import DayDiscoveryActionExecutor, DayDiscoveryLoop, DayDiscoveryLoopConfig
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_execution import GeneratedStrategyLimits
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.local_browser_gateway_config import (
    InvalidLocalBrowserGatewayConfigError,
    load_local_browser_gateway_config,
)
from trading_agent.research_agent_actions import (
    ResearchAgentActionConfig,
    ResearchAgentActionContext,
    ResearchAgentActionExecutor,
)
from trading_agent.research_agent_cycle_models import ResearchAgentResultV1
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_day_actions import DayResearchActionExecutor
from trading_agent.research_agent_decision import (
    ClaudeCliResearchAgentDecisionClient,
    HermesCliResearchAgentDecisionClient,
    ResearchAgentDecisionClient,
)
from trading_agent.research_agent_derivatives_actions import DerivativesResearchActionExecutor
from trading_agent.research_agent_primary_actions import (
    MarketContextResearchActionExecutor,
    OpportunityResearchActionExecutor,
)
from trading_agent.research_agent_runtime import (
    ConfiguredResearchAgentEvidenceCollector,
    ResearchAgentRuntime,
    ResearchAgentRuntimeServices,
)
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_service_models import InvalidResearchAgentServiceRuntimeError
from trading_agent.research_agent_swing_actions import SwingResearchActionExecutor
from trading_agent.research_agent_systematic import SystematicResearchActionExecutor
from trading_agent.researcher_llm import (
    FixtureLlmProposalClient,
    HermesCliProposalClient,
    StructuredHypothesisGenerator,
    load_private_canonical_llm_response,
    load_private_canonical_researcher_context,
)
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
    build_researcher_context,
    build_source_hypothesis_factory,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore
from trading_agent.strategy_research_work_sink import PrivateStrategyResearchWorkSink


def build_service_runtime(config: ResearchAgentServiceConfig) -> ResearchAgentRuntime:
    from trading_agent.research_agent_service_reporting import prepare_private_runtime_paths

    prepare_private_runtime_paths(config)
    with ExitStack() as ownership:
        cycle_store = ResearchAgentCycleStore(config.cycle_database)
        ownership.callback(cycle_store.close)
        systematic = SystematicResearchActionExecutor(config.systematic, prior_results=cycle_store.results)
        opportunity = OpportunityResearchActionExecutor(
            hypothesis_creator=build_source_hypothesis_factory(
                cycle_store.all_evidence,
                config.source_paths.kr_calendar_store,
            ),
            hypothesis_sink=PrivateStrategyResearchWorkSink(
                ExperimentLedgerStore(config.source_paths.experiment_ledger),
                config.source_paths.outputs_root / "strategy-research" / "work",
            ),
        )
        actions = ResearchAgentActionExecutor(
            ResearchAgentActionConfig(
                systematic=systematic,
                opportunity=opportunity,
                market_context=MarketContextResearchActionExecutor(cycle_store.results),
                day=DayResearchActionExecutor(
                    config.source_paths.day_session_root,
                    discovery=_ConfiguredDayDiscoveryAction(config),
                ),
                swing=SwingResearchActionExecutor(config.source_paths.swing_shadow_database),
                derivatives=DerivativesResearchActionExecutor(cycle_store.results),
            )
        )
        browser_path = config.browser_gateway_config
        if browser_path is None:
            supervisor = build_autonomous_supervisor(config)
        else:
            try:
                gateway = load_local_browser_gateway_config(browser_path)
                evidence_database = config.output_root / "autonomous-supervisor" / "browser-social-evidence.sqlite3"
                browser = BrowserToolServices(gateway.socket_path, evidence_database)
                supervisor = build_autonomous_supervisor(config, browser=browser)
                ownership.callback(supervisor.close)
                _ = BrowserSocialEvidenceStore(evidence_database).search("service-initialization", limit=1)
            except (InvalidBrowserSocialEvidenceStoreError, InvalidLocalBrowserGatewayConfigError):
                raise InvalidResearchAgentServiceRuntimeError from None
        if browser_path is None:
            ownership.callback(supervisor.close)
        installed_supervisor = (
            supervisor
            if browser_path is None
            else ContinuousBrowserResearchSupervisor(
                supervisor,
                cycle_store,
                owns_cycles=False,
                agenda_version=2 if config.schema_version == 4 else 1,
            )
        )
        runtime = ResearchAgentRuntime(
            ResearchAgentRuntimeServices(
                store=cycle_store,
                collector=ConfiguredResearchAgentEvidenceCollector(
                    config.source_paths,
                    systematic_review_root=config.systematic.review_root,
                ),
                decisions=_decision_client(config),
                actions=actions,
                supervisor_runtime=installed_supervisor,
            )
        )
        _ = ownership.pop_all()
        return runtime


def _day_discovery_executor(
    config: ResearchAgentServiceConfig,
    called_at: dt.datetime,
) -> DayDiscoveryActionExecutor:
    systematic = config.systematic
    receipts = ResearcherReceiptStore(systematic.receipt_root)
    ledger = ExperimentLedgerStore(config.source_paths.experiment_ledger)
    if systematic.response_fixture is not None:
        proposal_client = FixtureLlmProposalClient(load_private_canonical_llm_response(systematic.response_fixture))
    elif systematic.hermes_executable is not None:
        proposal_client = HermesCliProposalClient(
            systematic.hermes_executable,
            systematic.model_id,
            systematic.provider_id,
        )
    else:
        raise InvalidResearchAgentServiceRuntimeError
    source = load_private_canonical_researcher_context(systematic.context)
    runtime = resolve_generated_strategy_runtime(systematic.python_executable)
    strategies = GeneratedStrategyArtifactStore(systematic.strategy_root, runtime)
    pipeline = ResearcherPipeline(
        ResearcherPipelineServices(
            StructuredHypothesisGenerator(proposal_client, receipts, lambda: called_at),
            DeterministicHypothesisCritic(max_free_parameters=4),
        ),
        ResearcherPipelineStores(ledger, receipts, strategies),
        ResearcherPipelineArtifacts(systematic.manifest_root, systematic.queue_root),
    )
    return DayDiscoveryActionExecutor(
        DayDiscoveryLoop(
            DayDiscoveryLoopConfig(
                pipeline,
                GeneratedStrategySandbox(
                    runtime,
                    systematic.strategy_root / "day-sandbox",
                    GeneratedStrategyLimits(),
                ),
                3,
                clock=lambda: called_at,
            )
        ),
        build_researcher_context(source, ledger.reader()),
    )


@dataclass(frozen=True, slots=True)
class _ConfiguredDayDiscoveryAction:
    config: ResearchAgentServiceConfig

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        return _day_discovery_executor(self.config, context.observed_at).execute(context)


def _decision_client(config: ResearchAgentServiceConfig) -> ResearchAgentDecisionClient:
    if config.provider_id == "claude-code":
        return ClaudeCliResearchAgentDecisionClient(config.hermes_executable, config.model_id)
    return HermesCliResearchAgentDecisionClient(config.hermes_executable, config.model_id, config.provider_id)


__all__ = ("_day_discovery_executor", "build_service_runtime", "resolve_generated_strategy_runtime")
