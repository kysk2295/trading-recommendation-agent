from __future__ import annotations

from typing import Final, assert_never

from development_harness.task_contract import GrokTaskContract
from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle, KrLoopFailureCode

_EXPECTED_SUMMARY: Final = ("changed_files", "verification", "concerns")
_POLICY: Final = {
    KrLoopFailureCode.CRITIC_CHRONOLOGY: (
        "trading_agent/kr_social_signal_models.py",
        "tests/test_kr_social_signal.py",
    ),
    KrLoopFailureCode.CRITIC_CLUSTER_COUNT: (
        "trading_agent/kr_social_signal_models.py",
        "tests/test_kr_social_signal.py",
    ),
    KrLoopFailureCode.MARKET_DATA: (
        "trading_agent/kr_autonomous_market_service.py",
        "tests/test_kr_autonomous_market_service.py",
    ),
    KrLoopFailureCode.VIRTUAL_CENSORED: (
        "trading_agent/kr_virtual_position_engine.py",
        "tests/test_kr_virtual_position_engine.py",
    ),
    KrLoopFailureCode.VIRTUAL_STOP: (
        "trading_agent/kr_autonomous_trade_planner.py",
        "tests/test_kr_autonomous_trade_planner.py",
    ),
}


def mutation_contract(bundle: KrLoopEngineerEvidenceBundle, base_commit: str) -> GrokTaskContract:
    paths = _allowed_paths(bundle.failure_code)
    implementation = paths[0]
    test = paths[1]
    return GrokTaskContract(
        schema_version=1,
        task_id=f"kr-loop-{bundle.bundle_id[:16]}",
        base_commit=base_commit,
        objective=(
            f"Fix the repeated KR {bundle.failure_code.value} failure using only the cited evidence lineage. "
            f"Hypothesis: {bundle.change_hypothesis} Preserve every host safety and paper-only boundary."
        ),
        allowed_paths=paths,
        required_commands=(
            f"uv run pytest -q {test}",
            "uv run pytest -q tests/test_kr_autonomous_vertical.py",
            f"uv run ruff check {implementation} {test}",
            f"uv run basedpyright {implementation} {test}",
        ),
        manual_qa_commands=("uv run python run_research_agent_runtime.py --help",),
        expected_summary_fields=_EXPECTED_SUMMARY,
        max_turns=24,
    )


def _allowed_paths(failure: KrLoopFailureCode) -> tuple[str, ...]:
    match failure:
        case KrLoopFailureCode.CRITIC_CHRONOLOGY:
            return _POLICY[failure]
        case KrLoopFailureCode.CRITIC_CLUSTER_COUNT:
            return _POLICY[failure]
        case KrLoopFailureCode.MARKET_DATA:
            return _POLICY[failure]
        case KrLoopFailureCode.VIRTUAL_CENSORED:
            return _POLICY[failure]
        case KrLoopFailureCode.VIRTUAL_STOP:
            return _POLICY[failure]
        case unreachable:
            assert_never(unreachable)


__all__ = ("mutation_contract",)
