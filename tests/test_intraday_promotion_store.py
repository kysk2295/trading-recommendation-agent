from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.intraday_promotion_models import (
    IntradayPromotionApproval,
    PromotionApprovalContent,
    approval_id,
)
from trading_agent.intraday_promotion_store import (
    InvalidIntradayPromotionArtifactError,
    load_promotion_approval,
    publish_promotion_approval,
)


def _approval() -> IntradayPromotionApproval:
    content = PromotionApprovalContent(
        assessment_id="a" * 64,
        strategy_version="challenger.v1",
        decision_session_date=dt.date(2026, 7, 27),
        target_state=StrategyLifecycleState.SHADOW_CHAMPION,
        approver="operator_1",
        approved_at=dt.datetime(2026, 7, 27, 20, 35, tzinfo=dt.UTC),
    )
    return IntradayPromotionApproval(approval_id=approval_id(content), content=content)


def test_approval_publication_is_private_immutable_and_idempotent(tmp_path: Path) -> None:
    # Given: a distinct manual approval receipt
    approval = _approval()

    # When: it is durably published twice
    path, first = publish_promotion_approval(tmp_path / "approvals", approval)
    replay_path, replay = publish_promotion_approval(tmp_path / "approvals", approval)

    # Then: exactly one private canonical receipt exists
    assert (first, replay, path, replay_path, path.stat().st_mode & 0o777) == (
        True,
        False,
        replay_path,
        path,
        0o600,
    )
    assert load_promotion_approval(path) == approval


def test_approval_loader_rejects_a_hard_link(tmp_path: Path) -> None:
    # Given: a valid receipt with an extra filesystem link
    path, _ = publish_promotion_approval(tmp_path / "approvals", _approval())
    os.link(path, tmp_path / "alias.json")

    # When / Then: the authoritative loader fails closed
    with pytest.raises(InvalidIntradayPromotionArtifactError):
        _ = load_promotion_approval(path)
