from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.test_us_day_agent_tick_cli import _strategy_runtime
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.private_immutable_file import publish_private_immutable_text

if TYPE_CHECKING:
    from trading_agent.us_day_champion_bootstrap import UsDayChampionBootstrapRequest


@dataclass(frozen=True, slots=True)
class _BootstrapFixture:
    request: UsDayChampionBootstrapRequest
    capsule_id: str
    prompt: Path
    tool: Path
    memory: Path


def test_plan_binds_reviewed_capsule_model_and_exact_policy_hashes(tmp_path: Path) -> None:
    # Given: a reviewed US Day capsule and four owner-only evidence files.
    from trading_agent.us_day_champion_bootstrap import plan_us_day_champion_bootstrap

    fixture = _fixture(tmp_path)

    # When: the bootstrap is planned without mutating the version store.
    plan = plan_us_day_champion_bootstrap(fixture.request)

    # Then: the candidate Champion is bound to exact evidence and grants no order authority.
    assert plan.version.playbook_ids == (fixture.capsule_id,)
    assert plan.version.model_role_bindings[0].model_id == "openai-codex/gpt-5.5"
    assert plan.version.prompt_sha256 == _sha256(fixture.prompt)
    assert plan.version.tool_policy_sha256 == _sha256(fixture.tool)
    assert plan.version.memory_retrieval_policy_sha256 == _sha256(fixture.memory)
    assert plan.version.order_authority is False
    assert not fixture.request.version_store.exists()


def test_bootstrap_registers_once_and_replays_the_same_receipt(tmp_path: Path) -> None:
    # Given: one valid deterministic bootstrap plan.
    from trading_agent.us_day_champion_bootstrap import bootstrap_us_day_champion

    fixture = _fixture(tmp_path)

    # When: the same bootstrap is executed twice.
    first = bootstrap_us_day_champion(fixture.request)
    replay = bootstrap_us_day_champion(fixture.request)

    # Then: one Champion and one immutable authority-free receipt exist.
    assert first.version_created is True
    assert first.receipt_created is True
    assert replay.version_created is False
    assert replay.receipt_created is False
    assert DayAgentVersionStore(fixture.request.version_store).reader().champion() == first.receipt.version
    receipts = tuple(fixture.request.receipt_root.glob("champion_bootstrap_*.json"))
    assert len(receipts) == 1
    assert receipts[0].stat().st_mode & 0o777 == 0o600
    assert first.receipt.order_authority is False
    assert first.receipt.paper_trading_enabled is False


def test_bootstrap_rejects_unsafe_review_evidence_before_store_creation(tmp_path: Path) -> None:
    # Given: review evidence that is not an owner-only private file.
    from trading_agent.us_day_champion_bootstrap import (
        UsDayChampionBootstrapError,
        plan_us_day_champion_bootstrap,
    )

    fixture = _fixture(tmp_path)
    fixture.request.review_evidence.chmod(0o644)

    # When / Then: planning fails closed before creating a version database.
    with pytest.raises(UsDayChampionBootstrapError, match="champion_bootstrap_invalid"):
        plan_us_day_champion_bootstrap(fixture.request)
    assert not fixture.request.version_store.exists()


def test_plan_rejects_a_session_older_than_the_latest_completed_xnys_session(
    tmp_path: Path,
) -> None:
    # Given: a bootstrap timestamp after the August 20 close but an August 19 session claim.
    from trading_agent.us_day_champion_bootstrap import (
        UsDayChampionBootstrapError,
        plan_us_day_champion_bootstrap,
    )

    fixture = _fixture(tmp_path)
    stale = fixture.request.model_copy(update={"created_session_date": dt.date(2026, 8, 19)})

    # When / Then: planning rejects the stale session without creating a store.
    with pytest.raises(UsDayChampionBootstrapError, match="champion_bootstrap_invalid"):
        plan_us_day_champion_bootstrap(stale)
    assert not fixture.request.version_store.exists()


def test_receipt_conflict_cannot_leave_a_champion_without_its_receipt(tmp_path: Path) -> None:
    # Given: an immutable conflicting file at the planned receipt identity.
    from trading_agent.us_day_champion_bootstrap import (
        UsDayChampionBootstrapError,
        bootstrap_us_day_champion,
        plan_us_day_champion_bootstrap,
    )

    fixture = _fixture(tmp_path)
    plan = plan_us_day_champion_bootstrap(fixture.request)
    receipt = fixture.request.receipt_root / f"champion_bootstrap_{plan.version.version_id}.json"
    assert publish_private_immutable_text(receipt, '{"conflict":true}\n')

    # When / Then: publication failure happens before any Champion store mutation.
    with pytest.raises(UsDayChampionBootstrapError, match="champion_bootstrap_invalid"):
        bootstrap_us_day_champion(fixture.request)
    assert not fixture.request.version_store.exists()


def test_receipt_only_recovery_blocks_a_different_bootstrap_intent(tmp_path: Path) -> None:
    # Given: receipt publication succeeds while another process holds the version writer lease.
    from trading_agent.us_day_champion_bootstrap import (
        UsDayChampionBootstrapError,
        bootstrap_us_day_champion,
    )

    fixture = _fixture(tmp_path)
    store = DayAgentVersionStore(fixture.request.version_store)
    with store.writer(), pytest.raises(UsDayChampionBootstrapError, match="champion_bootstrap_invalid"):
        bootstrap_us_day_champion(fixture.request)
    assert store.reader().champion() is None
    assert len(tuple(fixture.request.receipt_root.glob("champion_bootstrap_*.json"))) == 1
    different = fixture.request.model_copy(update={"reasoning_model_id": "other-reasoner-v1"})

    # When / Then: a different intent is blocked and the exact original intent resumes safely.
    with pytest.raises(UsDayChampionBootstrapError, match="champion_bootstrap_invalid"):
        bootstrap_us_day_champion(different)
    recovered = bootstrap_us_day_champion(fixture.request)
    assert recovered.receipt_created is False
    assert recovered.version_created is True
    assert store.reader().champion() == recovered.receipt.version
    assert len(tuple(fixture.request.receipt_root.glob("champion_bootstrap_*.json"))) == 1


def _fixture(tmp_path: Path) -> _BootstrapFixture:
    from trading_agent.us_day_champion_bootstrap import UsDayChampionBootstrapRequest

    _version, playbook, strategy_manifest, experiment_ledger = _strategy_runtime(tmp_path)
    prompt = _private(tmp_path / "prompt-policy.json", '{"policy":"day-reasoning-v1"}')
    tool = _private(tmp_path / "tool-policy.json", '{"tools":"canonical-read-only-v1"}')
    memory = _private(tmp_path / "memory-policy.json", '{"memory":"immutable-outcomes-v1"}')
    review = _private(tmp_path / "review-evidence.json", '{"decision":"approved_for_observer"}')
    request = UsDayChampionBootstrapRequest(
        strategy_manifest=strategy_manifest,
        experiment_ledger=experiment_ledger,
        version_store=tmp_path / "versions" / "versions.sqlite3",
        reasoning_model_id="openai-codex/gpt-5.5",
        prompt_policy=prompt,
        tool_policy=tool,
        memory_policy=memory,
        review_evidence=review,
        receipt_root=tmp_path / "receipts",
        created_at=dt.datetime(2026, 8, 21, 0, tzinfo=dt.UTC),
        created_session_date=dt.date(2026, 8, 20),
    )
    return _BootstrapFixture(request, playbook.playbook_id, prompt, tool, memory)


def _private(path: Path, payload: str) -> Path:
    assert publish_private_immutable_text(path, payload + "\n")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
