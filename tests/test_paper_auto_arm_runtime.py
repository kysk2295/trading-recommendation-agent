from __future__ import annotations

import datetime as dt
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import trading_agent.paper_auto_arm_runtime as runtime_module
from tests.test_paper_auto_arm_policy import _authority
from trading_agent.hermes_arm_request import (
    HermesArmConsumeCommand,
    HermesArmFailure,
    HermesArmScope,
    InvalidHermesArmRequestError,
)
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_auto_arm_consumption_store import PaperAutoArmConsumptionStore
from trading_agent.paper_auto_arm_policy import PaperAutoArmPolicy
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE
from trading_agent.us_day_operating_models import UsDayOperatingStatus

NOW = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.UTC)
SESSION = "XNYS-2026-07-14"


def _consume_auto_arm_in_process(store_root: str) -> str:
    authority = _authority()
    minted = runtime_module.mint_paper_auto_arm_consumer(
        PaperAutoArmPolicy.from_authority(authority),
        authority,
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(Path(store_root)),
    )
    command = HermesArmConsumeCommand(
        request_id=minted.request_id,
        expected_scope=authority.scope,
    )
    try:
        _ = minted.consumer.consume(command, authority.strategy_version)
    except InvalidHermesArmRequestError as error:
        return error.reason.value
    return "armed"


def test_current_session_policy_rejects_replay_from_a_second_consumer(
    tmp_path: Path,
) -> None:
    # Given: a matching enabled standing policy and current-session authority.
    policy = PaperAutoArmPolicy.from_authority(_authority())

    # When: two independent consumers derive the same request identity and one consumes it.
    store_root = tmp_path / "consumptions"
    first = runtime_module.mint_paper_auto_arm_consumer(
        policy,
        _authority(),
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(store_root),
    )
    second = runtime_module.mint_paper_auto_arm_consumer(
        policy,
        _authority(),
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(store_root),
    )
    command = HermesArmConsumeCommand(
        request_id=first.request_id,
        expected_scope=HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM),
    )
    arm = first.consumer.consume(command, "orb-v1")

    # Then: the request identity is deterministic and no second consumer can replay it.
    assert first.request_id == second.request_id
    assert arm.value == PAPER_MUTATION_ARM_VALUE
    with pytest.raises(InvalidHermesArmRequestError) as replay:
        second.consumer.consume(command, "orb-v1")
    assert replay.value.reason is HermesArmFailure.CONSUMED


def test_current_session_policy_has_one_winner_across_processes(
    tmp_path: Path,
) -> None:
    # Given: independent processes using one execution-bound consumption store.
    context = multiprocessing.get_context("spawn")
    store_root = str((tmp_path / "consumptions").resolve())

    # When: four workers race to consume the deterministic current-session arm.
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        outcomes = list(executor.map(_consume_auto_arm_in_process, [store_root] * 4))

    # Then: exactly one process receives an arm and every replay fails closed.
    assert outcomes.count("armed") == 1
    assert outcomes.count(HermesArmFailure.CONSUMED.value) == 3


def test_auto_arm_store_rejects_public_or_linked_root(tmp_path: Path) -> None:
    authority = _authority()
    policy = PaperAutoArmPolicy.from_authority(authority)
    command = HermesArmConsumeCommand(
        request_id=runtime_module.paper_auto_arm_request_id(policy, SESSION),
        expected_scope=authority.scope,
    )
    public_root = tmp_path / "public"
    public_root.mkdir(mode=0o755)
    public_root.chmod(0o755)
    linked_root = tmp_path / "linked"
    private_target = tmp_path / "private"
    private_target.mkdir(mode=0o700)
    linked_root.symlink_to(private_target, target_is_directory=True)

    for root in (public_root, linked_root):
        minted = runtime_module.mint_paper_auto_arm_consumer(
            policy,
            authority,
            SESSION,
            NOW,
            PaperAutoArmConsumptionStore(root),
        )
        with pytest.raises(InvalidHermesArmRequestError) as blocked:
            _ = minted.consumer.consume(command, authority.strategy_version)
        assert blocked.value.reason is HermesArmFailure.INVALID_STORE


def test_auto_arm_store_rejects_hard_linked_receipt(tmp_path: Path) -> None:
    authority = _authority()
    policy = PaperAutoArmPolicy.from_authority(authority)
    store_root = tmp_path / "consumptions"
    minted = runtime_module.mint_paper_auto_arm_consumer(
        policy,
        authority,
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(store_root),
    )
    command = HermesArmConsumeCommand(
        request_id=minted.request_id,
        expected_scope=authority.scope,
    )
    _ = minted.consumer.consume(command, authority.strategy_version)
    receipt = store_root / f"{minted.request_id}.json"
    (tmp_path / "receipt-copy.json").hardlink_to(receipt)

    replay = runtime_module.mint_paper_auto_arm_consumer(
        policy,
        authority,
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(store_root),
    )
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        _ = replay.consumer.consume(command, authority.strategy_version)
    assert blocked.value.reason is HermesArmFailure.INVALID_STORE


@pytest.mark.parametrize(
    "session,now",
    (
        ("XNYS-2026-07-13", NOW),
        (SESSION, dt.datetime(2026, 7, 15, 14, 0, tzinfo=dt.UTC)),
        ("XNYS-2026-07-18", dt.datetime(2026, 7, 18, 14, 0, tzinfo=dt.UTC)),
    ),
)
def test_auto_arm_rejects_wrong_or_closed_current_session(
    session: str,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    # Given: policy authority with a non-current or closed XNYS session.
    policy = PaperAutoArmPolicy.from_authority(_authority())

    # When / Then: minting stops before any coordinator can receive an arm.
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        runtime_module.mint_paper_auto_arm_consumer(
            policy,
            _authority(),
            session,
            now,
            PaperAutoArmConsumptionStore(tmp_path / "consumptions"),
        )
    assert blocked.value.reason is HermesArmFailure.WRONG_SESSION


@pytest.mark.parametrize(
    "request_id,scope,strategy,reason",
    (
        ("0" * 64, HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM), "orb-v1", "invalid_request"),
        (
            "derived",
            HermesArmScope(session_id="XNYS-2026-07-15", lane_id=LaneId.INTRADAY_MOMENTUM),
            "orb-v1",
            "wrong_session",
        ),
        (
            "derived",
            HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM),
            "other-v1",
            "champion_mismatch",
        ),
    ),
)
def test_auto_arm_consumer_rejects_wrong_request_scope_or_strategy(
    request_id: str,
    scope: HermesArmScope,
    strategy: str,
    reason: str,
    tmp_path: Path,
) -> None:
    # Given: one minted session-scoped consumer.
    minted = runtime_module.mint_paper_auto_arm_consumer(
        PaperAutoArmPolicy.from_authority(_authority()),
        _authority(),
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(tmp_path / "consumptions"),
    )
    supplied_id = minted.request_id if request_id == "derived" else request_id

    # When / Then: a binding mismatch is rejected before the arm is marked consumed.
    with pytest.raises(InvalidHermesArmRequestError) as blocked:
        minted.consumer.consume(
            HermesArmConsumeCommand(request_id=supplied_id, expected_scope=scope),
            strategy,
        )
    assert blocked.value.reason.value == reason
    valid = HermesArmConsumeCommand(
        request_id=minted.request_id,
        expected_scope=HermesArmScope(session_id=SESSION, lane_id=LaneId.INTRADAY_MOMENTUM),
    )
    assert minted.consumer.consume(valid, "orb-v1").value == PAPER_MUTATION_ARM_VALUE


def test_policy_one_shot_arm_reaches_existing_operating_coordinator(tmp_path: Path) -> None:
    # Given: a minted policy consumer and the existing coordinator with an in-memory Paper session.
    from tests.us_day_operating_fixtures import NaturalPaperSession, OperatingHarness, admission, operating_request

    order_admission = admission()
    authority = _authority().model_copy(update={"strategy_version": order_admission.candidate_intent.strategy_version})
    minted = runtime_module.mint_paper_auto_arm_consumer(
        PaperAutoArmPolicy.from_authority(authority),
        authority,
        SESSION,
        NOW,
        PaperAutoArmConsumptionStore(tmp_path / "consumptions"),
    )
    request = replace(operating_request(order_admission), arm_request_id=minted.request_id)

    # When: the existing operating coordinator drives the session under fakes.
    result, _ = OperatingHarness(tmp_path, NaturalPaperSession(order_admission)).run(request, minted.consumer)

    # Then: the coordinator consumes the durable arm and reaches a reconciled terminal.
    assert result.status is UsDayOperatingStatus.COMPLETED
