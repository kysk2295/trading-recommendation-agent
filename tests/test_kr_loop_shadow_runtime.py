from __future__ import annotations

import os
from pathlib import Path

from tests.test_kr_loop_evaluation import NOW, _outcome
from tests.test_research_agent_service_cli import _config
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_outcome_memory import outcome_record
from trading_agent.kr_autonomous_outcome_models import KrLoopFailureCode
from trading_agent.kr_loop_shadow_runtime import run_shadow_session
from trading_agent.research_agent_service_config import load_research_agent_service_config


def test_shadow_runtime_runs_isolated_lanes_sequentially_and_records_actual_outcomes(tmp_path: Path) -> None:
    config = _config(tmp_path / "base")
    champion = _source(tmp_path / "champion")
    challenger = _source(tmp_path / "challenger")
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], environment: dict[str, str]) -> int:
        calls.append(command)
        config_path = Path(command[-1])
        lane = load_research_agent_service_config(config_path)
        clusters = 1 if "champion" in str(lane.output_root) else 3
        memory = AutonomousMemoryStore(lane.output_root / "autonomous-supervisor" / "memory.sqlite3")
        record = outcome_record(memory, _outcome(str(clusters), clusters=clusters))
        assert record is not None
        with memory.writer() as writer:
            assert writer.append(record)
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        assert environment["PYTHONPATH"].split(os.pathsep)[0] in {str(champion), str(challenger)}
        return 0

    result = run_shadow_session(
        base_config=config,
        champion_root=champion,
        challenger_root=challenger,
        shadow_root=tmp_path / "shadow",
        candidate_id="f" * 64,
        failure_code=KrLoopFailureCode.CRITIC_CLUSTER_COUNT,
        session_date=NOW.date(),
        observed_at=NOW,
        runner=runner,
    )

    assert result.receipt is not None
    assert result.receipt.challenger_score > result.receipt.champion_score
    assert len(calls) == 2
    assert "champion" in calls[0][-1]
    assert "challenger" in calls[1][-1]
    assert calls[0][-1] != calls[1][-1]


def test_shadow_runtime_does_not_fabricate_receipt_when_lane_has_no_outcome(tmp_path: Path) -> None:
    config = _config(tmp_path / "base")
    result = run_shadow_session(
        base_config=config,
        champion_root=_source(tmp_path / "champion"),
        challenger_root=_source(tmp_path / "challenger"),
        shadow_root=tmp_path / "shadow",
        candidate_id="e" * 64,
        failure_code=KrLoopFailureCode.MARKET_DATA,
        session_date=NOW.date(),
        observed_at=NOW,
        runner=lambda _command, _environment: 0,
    )

    assert result.receipt is None
    assert result.status == "evidence_pending"


def _source(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "run_research_agent_runtime.py").write_text("print('fixture')\n", encoding="utf-8")
    return path
