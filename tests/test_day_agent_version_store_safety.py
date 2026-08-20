from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from tests.day_agent_version_learning_support import SESSION, champion
from tests.test_day_learning_report_models import NOW, SHA_A
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentDeploymentTransition,
    AgentEvaluationMetrics,
    AgentPromotionDecision,
    AgentPromotionRecommendation,
    AgentScoreComparison,
    DayAgentVersionStoreError,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore

_REGISTER_INITIAL: Final = """
import sqlite3
import sys
from tests.day_agent_version_learning_support import champion
from trading_agent.day_agent_version_models import DayAgentVersionStoreError
from trading_agent.day_agent_version_store import DayAgentVersionStore
try:
    with DayAgentVersionStore(__import__('pathlib').Path(sys.argv[1])).writer() as writer:
        print('created' if writer.register_initial_champion(champion()) else 'replay')
except DayAgentVersionStoreError as error:
    print(error.reason)
except sqlite3.OperationalError:
    print('raw_sqlite_operational_error')
"""
_REGISTER_CHALLENGER: Final = """
import sqlite3
import sys
from pathlib import Path
from trading_agent.day_agent_version_models import AgentVersion, DayAgentVersionStoreError
from trading_agent.day_agent_version_store import DayAgentVersionStore
version = AgentVersion.model_validate_json(sys.argv[2])
try:
    with DayAgentVersionStore(Path(sys.argv[1])).writer() as writer:
        print('created' if writer.register_challenger(version) else 'replay')
except DayAgentVersionStoreError as error:
    print(error.reason)
except sqlite3.OperationalError:
    print('raw_sqlite_operational_error')
"""
_APPLY_PROMOTION: Final = """
import sqlite3
import sys
from pathlib import Path
from trading_agent.day_agent_version_models import (
    AgentDeploymentTransition, AgentPromotionRecommendation, DayAgentVersionStoreError,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
recommendation = AgentPromotionRecommendation.model_validate_json(sys.argv[2])
transition = AgentDeploymentTransition.model_validate_json(sys.argv[3])
try:
    with DayAgentVersionStore(Path(sys.argv[1])).writer() as writer:
        print('created' if writer._apply_promotion(recommendation, transition) else 'replay')
except DayAgentVersionStoreError as error:
    print(error.reason)
except sqlite3.OperationalError:
    print('raw_sqlite_operational_error')
"""


def test_version_store_rejects_root_and_intermediate_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    (target / "nested").mkdir(mode=0o700)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    outer = tmp_path / "outer"
    outer.mkdir(mode=0o700)
    intermediate = outer / "intermediate"
    intermediate.symlink_to(target, target_is_directory=True)

    for path in (root_link / "versions.sqlite3", intermediate / "nested" / "versions.sqlite3"):
        with (
            pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"),
            DayAgentVersionStore(path).writer(),
        ):
            pass
        with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"):
            _ = DayAgentVersionStore(path).reader().champion()


def test_version_store_rejects_unsafe_existing_directory_without_chmod(tmp_path: Path) -> None:
    root = tmp_path / "unsafe-root"
    root.mkdir(mode=0o755)

    with (
        pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"),
        DayAgentVersionStore(root / "versions.sqlite3").writer(),
    ):
        pass

    assert root.stat().st_mode & 0o777 == 0o755


def test_version_store_rejects_internal_lock_symlink(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    target = tmp_path / "lock-target"
    target.write_text("x")
    target.chmod(0o600)
    (root / "versions.sqlite3.writer.lock").symlink_to(target)

    with (
        pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"),
        DayAgentVersionStore(root / "versions.sqlite3").writer(),
    ):
        pass


def test_version_store_rejects_root_namespace_replacement_on_reopen(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = DayAgentVersionStore(root / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
    displaced = tmp_path / "displaced"
    root.rename(displaced)
    root.mkdir(mode=0o700)
    shutil.copyfile(displaced / "versions.sqlite3", root / "versions.sqlite3")
    (root / "versions.sqlite3").chmod(0o600)

    reopened = DayAgentVersionStore(root / "versions.sqlite3")
    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"), reopened.writer():
        pass
    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"):
        _ = reopened.reader().champion()


def test_version_store_rejects_database_replacement_on_reopen(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = DayAgentVersionStore(root / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
    replacement = root / "replacement.sqlite3"
    shutil.copyfile(store.path, replacement)
    replacement.chmod(0o600)
    os.replace(replacement, store.path)

    reopened = DayAgentVersionStore(root / "versions.sqlite3")
    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"), reopened.writer():
        pass


def test_concurrent_initial_champion_is_serialized_without_raw_sqlite_error(tmp_path: Path) -> None:
    path = tmp_path / "store" / "versions.sqlite3"
    store = DayAgentVersionStore(path)

    with store.writer() as writer:
        assert _run_child(_REGISTER_INITIAL, path) == "version_store_writer_busy"
        assert writer.register_initial_champion(champion())

    assert _run_child(_REGISTER_INITIAL, path) == "replay"
    assert store.reader().champion() == champion()


def test_concurrent_challenger_is_serialized_and_replay_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "store" / "versions.sqlite3"
    store = DayAgentVersionStore(path)
    baseline = champion()
    challenger = _challenger(baseline)
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
    with store.writer() as writer:
        assert _run_child(_REGISTER_CHALLENGER, path, challenger.model_dump_json()) == "version_store_writer_busy"
        assert writer.register_challenger(challenger)

    assert _run_child(_REGISTER_CHALLENGER, path, challenger.model_dump_json()) == "replay"
    assert store.reader().challengers() == (challenger,)


def test_concurrent_promotion_yields_one_atomic_transition_and_defined_conflict(tmp_path: Path) -> None:
    path = tmp_path / "store" / "versions.sqlite3"
    store = DayAgentVersionStore(path)
    baseline = champion()
    challenger = _challenger(baseline)
    recommendation, transition = _promotion(baseline.version_id, challenger.version_id)
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
        assert writer.register_challenger(challenger)
        assert writer._record_controller_recommendation(recommendation)
    with store.writer() as writer:
        assert _run_child(
            _APPLY_PROMOTION,
            path,
            recommendation.model_dump_json(),
            transition.model_dump_json(),
        ) == "version_store_writer_busy"
        assert writer._apply_promotion(recommendation, transition)

    assert _run_child(
        _APPLY_PROMOTION,
        path,
        recommendation.model_dump_json(),
        transition.model_dump_json(),
    ) == "deployment_recommendation_invalid"
    assert store.reader().transitions() == (transition,)
    assert store.reader().champion() == challenger


def _run_child(script: str, path: Path, *payloads: str) -> str:
    completed = subprocess.run(
        (sys.executable, "-c", script, str(path), *payloads),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _challenger(baseline):
    return build_agent_version(
        model_role_bindings=baseline.model_role_bindings,
        prompt_sha256="5" * 64,
        tool_policy_sha256=baseline.tool_policy_sha256,
        memory_retrieval_policy_sha256=baseline.memory_retrieval_policy_sha256,
        playbook_ids=baseline.playbook_ids,
        parent_version_id=baseline.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=baseline.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )


def _promotion(
    champion_id: str,
    challenger_id: str,
) -> tuple[AgentPromotionRecommendation, AgentDeploymentTransition]:
    metrics = AgentEvaluationMetrics(
        theme_timing=0.8,
        leader_rank=0.8,
        recommendation_calibration=0.8,
        mfe=0.1,
        mae=-0.01,
        cost_adjusted_modeled_result=0.1,
        no_trade_quality=0.8,
        evidence_fidelity=1.0,
        provenance_ids=("7" * 64,),
    )
    recommendation = AgentPromotionRecommendation(
        recommendation_id="9" * 64,
        champion_version_id=champion_id,
        challenger_version_id=challenger_id,
        decision=AgentPromotionDecision.PROMOTE,
        evaluated_session_dates=(dt.date(2026, 8, 21), dt.date(2026, 8, 24)),
        paired_snapshot_ids=("snapshot-1", "snapshot-2"),
        controller_evidence_ids=("7" * 64,),
        comparison=AgentScoreComparison(champion=metrics, challenger=metrics),
        reason_codes=("challenger_margin_met",),
        evaluated_at=NOW + dt.timedelta(days=4),
    )
    return recommendation, AgentDeploymentTransition(
        transition_id="8" * 64,
        recommendation_id=recommendation.recommendation_id,
        demoted_version_id=champion_id,
        promoted_version_id=challenger_id,
        deployed_at=recommendation.evaluated_at,
    )
