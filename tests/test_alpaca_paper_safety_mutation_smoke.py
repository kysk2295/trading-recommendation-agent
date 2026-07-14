from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import cast

import pytest

import run_alpaca_paper_safety_mutation_smoke as cli
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.paper_mutation_executor_models import (
    PaperMutationExecutionResult,
    PaperMutationExecutionState,
)
from trading_agent.paper_mutation_keys import PaperMutationKey
from trading_agent.paper_operating_mutation_models import PaperSafetyMutationExecution
from trading_agent.paper_operating_session_models import (
    PaperOperatingSession,
    PaperPostMutationReconciliationError,
)
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_stream_recovery_runtime import PaperStreamRecoveryIncompleteError


@dataclass(slots=True)
class _FakeSession(AbstractContextManager["_FakeSession"]):
    result: PaperSafetyMutationExecution | BlockedPaperSafetyPlan | BaseException
    calls: list[tuple[PaperMutationArm, PaperRiskConfig]]

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute_safety_actions(
        self,
        arm: PaperMutationArm,
        config: PaperRiskConfig,
    ) -> PaperSafetyMutationExecution | BlockedPaperSafetyPlan:
        self.calls.append((arm, config))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _arguments(database: Path, output_dir: Path, *, arm: str = PAPER_MUTATION_ARM_VALUE) -> list[str]:
    return [
        "--arm-paper-mutation",
        arm,
        "--database",
        str(database),
        "--output-dir",
        str(output_dir),
    ]


def test_safety_mutation_smoke_requires_initialized_ledger_before_credentials(
    tmp_path: Path,
) -> None:
    credentials_loaded = False

    def credentials() -> AlpacaPaperCredentials:
        nonlocal credentials_loaded
        credentials_loaded = True
        return AlpacaPaperCredentials("key", "secret")

    output_dir = tmp_path / "report"
    exit_code = cli.main(
        _arguments(tmp_path / "missing.sqlite3", output_dir),
        credential_loader=credentials,
    )

    assert exit_code == 1
    assert credentials_loaded is False
    assert "결합된 실행 원장이 없습니다" in _report(output_dir)


def test_safety_mutation_smoke_rejects_wrong_arm_before_credentials(tmp_path: Path) -> None:
    credentials_loaded = False

    def credentials() -> AlpacaPaperCredentials:
        nonlocal credentials_loaded
        credentials_loaded = True
        return AlpacaPaperCredentials("key", "secret")

    with pytest.raises(SystemExit) as captured:
        _ = cli.main(
            _arguments(tmp_path / "execution.sqlite3", tmp_path / "report", arm="WRONG"),
            credential_loader=credentials,
        )

    assert captured.value.code == 2
    assert credentials_loaded is False


def test_safety_mutation_smoke_executes_reduced_risk_plan_and_reports_ack(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    calls: list[tuple[PaperMutationArm, PaperRiskConfig]] = []
    session = _FakeSession(
        _execution(
            PaperMutationExecutionState.ACKNOWLEDGED,
            PaperMutationExecutionState.ALREADY_ACKNOWLEDGED,
        ),
        calls,
    )

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    report = _report(tmp_path / "report")
    assert exit_code == 0
    assert calls[0][0] == PaperMutationArm(PAPER_MUTATION_ARM_VALUE)
    assert calls[0][1].max_notional_dollars == 100.0
    assert calls[0][1].max_risk_dollars == 10.0
    assert calls[0][1].max_open_positions == 1
    assert calls[0][1].daily_loss_limit_dollars == 30.0
    assert calls[0][1].per_side_cost_bps == 20.0
    assert "결과: acknowledged" in report
    assert "단계: eod_flatten" in report
    assert "조치 수: 2" in report
    assert "AAA: 신규진입 주문 취소 -> acknowledged" in report
    assert "AAA: sell 10주 평탄화 -> already_acknowledged" in report
    assert str(FINGERPRINT) not in report
    assert "entry-1" not in report
    assert "a" * 64 not in report
    assert "secret" not in report


@pytest.mark.parametrize(
    "state",
    (PaperMutationExecutionState.AMBIGUOUS, PaperMutationExecutionState.REJECTED),
)
def test_safety_mutation_smoke_returns_two_for_non_acknowledged_result(
    tmp_path: Path,
    state: PaperMutationExecutionState,
) -> None:
    store = initialized_store(tmp_path)
    session = _FakeSession(_execution(state), [])

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    assert exit_code == 2
    assert f"결과: {state.value}" in _report(tmp_path / "report")


def test_safety_mutation_smoke_never_treats_partial_ack_as_success(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    session = _FakeSession(_execution(PaperMutationExecutionState.ACKNOWLEDGED), [])

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    report = _report(tmp_path / "report")
    assert exit_code == 2
    assert "결과: incomplete" in report
    assert "AAA: sell 10주 평탄화 -> not_attempted" in report


def test_safety_mutation_smoke_redacts_current_epoch_block_details(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    sensitive = _sensitive_values()
    session = _FakeSession(BlockedPaperSafetyPlan((" ".join(sensitive),)), [])

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    report = _report(tmp_path / "report")
    assert exit_code == 1
    assert "결과: 차단" in report
    assert "current-epoch 안전 게이트가 실행을 차단했습니다" in report
    assert all(value not in report for value in sensitive)


def test_safety_mutation_smoke_redacts_exception_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = initialized_store(tmp_path)
    sensitive = _sensitive_values()
    session = _FakeSession(PaperStreamRecoveryIncompleteError((" ".join(sensitive),)), [])

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    rendered = _report(tmp_path / "report") + capsys.readouterr().err
    assert exit_code == 2
    assert "PaperStreamRecoveryIncompleteError" in rendered
    assert all(value not in rendered for value in sensitive)


def test_safety_mutation_smoke_returns_two_after_mutation_reconciliation_failure(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    session = _FakeSession(PaperPostMutationReconciliationError(), [])

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    assert exit_code == 2
    assert "PaperPostMutationReconciliationError" in _report(tmp_path / "report")


def test_safety_mutation_smoke_reports_no_action_without_broker_mutation(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    plan = _plan(actions=())
    session = _FakeSession(
        PaperSafetyMutationExecution(plan, (), (), OBSERVED_AT + dt.timedelta(seconds=1)),
        [],
    )

    exit_code = cli.main(
        _arguments(store.path, tmp_path / "report"),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=_opener(session),
    )

    assert exit_code == 0
    assert "결과: no_action_required" in _report(tmp_path / "report")
    assert "조치 수: 0" in _report(tmp_path / "report")


def _plan(
    *,
    actions: tuple[PaperCancelOrderAction | PaperClosePositionAction, ...] | None = None,
) -> PaperSafetyPlan:
    planned_actions = actions
    if planned_actions is None:
        planned_actions = (
            PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),
            PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10)),
        )
    return PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.EOD_FLATTEN,
        Decimal("-5"),
        Decimal("-7.5"),
        planned_actions,
    )


def _execution(
    *states: PaperMutationExecutionState,
) -> PaperSafetyMutationExecution:
    results = tuple(
        PaperMutationExecutionResult(
            PaperMutationKey(chr(ord("a") + index) * 64),
            state,
            None,
        )
        for index, state in enumerate(states)
    )
    return PaperSafetyMutationExecution(
        _plan(),
        results,
        (),
        OBSERVED_AT + dt.timedelta(seconds=1),
    )


def _opener(
    session: _FakeSession,
) -> Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]:
    def open_session(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        return cast(AbstractContextManager[PaperOperatingSession], session)

    return open_session


def _report(output_dir: Path) -> str:
    return (output_dir / "paper_safety_mutation_smoke_ko.md").read_text(encoding="utf-8")


def _sensitive_values() -> tuple[str, ...]:
    return (
        "fixture-secret-key",
        str(FINGERPRINT),
        "broker-order-secret",
        "request-id-secret",
        "c" * 64,
        '{"raw_payload":"secret"}',
    )
