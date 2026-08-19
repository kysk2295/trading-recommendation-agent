from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_research_agent_service_cli import _config
from tests.test_strategy_lab_research_kernel import _bundle
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_agent_configured_collector import (
    ConfiguredResearchAgentEvidenceCollector,
)
from trading_agent.strategy_lab_kernel import StrategyLabFleet
from trading_agent.strategy_lab_models import EvidenceMode


def test_strategy_lab_results_remain_outside_the_production_systematic_source_map(
    tmp_path: Path,
) -> None:
    # Given: one terminal six-lab diagnostic cycle in the shared experiment ledger.
    config = _config(tmp_path)
    fleet = StrategyLabFleet(ExperimentLedgerStore(config.source_paths.experiment_ledger))
    now = dt.datetime(2026, 8, 17, 21, 0, tzinfo=dt.UTC)
    _ = fleet.run_cycle(_bundle(EvidenceMode.HISTORICAL), now)
    collector = ConfiguredResearchAgentEvidenceCollector(config.source_paths)

    # When: the production collector reads its configured source map.
    batch = collector.collect(now + dt.timedelta(seconds=30))

    # Then: synchronized diagnostic traces never become systematic production evidence.
    feedback = tuple(item for item in batch.evidence if item.source_key.startswith("systematic.strategy_lab."))
    assert feedback == ()
    assert any(item.source_key == "systematic.blocked.research_evidence_empty" for item in batch.evidence)
