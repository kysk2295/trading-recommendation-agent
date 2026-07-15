from __future__ import annotations

import datetime as dt
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import cast

import run_alpaca_paper_protective_oco_smoke as cli
from tests.trade_update_ledger_fixtures import OBSERVED_AT, initialized_store, intent
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import BrokerOrderId, IntentId, PaperOrderSide
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.paper_mutation_executor_models import (
    PaperMutationExecutionResult,
    PaperMutationExecutionState,
)
from trading_agent.paper_mutation_keys import PaperMutationKey
from trading_agent.paper_operating_mutation_models import (
    PaperProtectiveCancelMutationExecution,
    PaperProtectiveMutationExecution,
)
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_protective_exit import BlockedProtectiveExitPlan, NoProtectiveExitRequired
from trading_agent.paper_protective_oco_lifecycle import ProtectiveOcoResizeCancelPlan
from trading_agent.paper_protective_oco_models import ProtectiveOcoClientOrderId, ProtectiveOcoExitPlan
from trading_agent.paper_protective_oco_store import ProtectiveOcoPlanKey


@dataclass(frozen=True, slots=True)
class _FakeSession(AbstractContextManager["_FakeSession"]):
    result: (
        PaperProtectiveMutationExecution
        | PaperProtectiveCancelMutationExecution
        | BlockedProtectiveExitPlan
        | NoProtectiveExitRequired
    )
    calls: list[tuple[IntentId, PaperMutationArm]]

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute_protective_oco(
        self,
        parent_intent_id: IntentId,
        arm: PaperMutationArm,
    ) -> (
        PaperProtectiveMutationExecution
        | PaperProtectiveCancelMutationExecution
        | BlockedProtectiveExitPlan
        | NoProtectiveExitRequired
    ):
        self.calls.append((parent_intent_id, arm))
        return self.result


def test_protective_oco_smoke_requires_an_initialized_ledger(tmp_path: Path) -> None:
    output_dir = tmp_path / "report"

    exit_code = cli.main(
        [
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(tmp_path / "missing.sqlite3"),
            "--output-dir",
            str(output_dir),
            "--intent-id",
            "orb-AAA-20260714-093500",
        ]
    )

    assert exit_code == 1
    assert "결합된 실행 원장이 없습니다" in (output_dir / "paper_protective_oco_smoke_ko.md").read_text(
        encoding="utf-8"
    )


def test_protective_oco_smoke_arms_exact_intent_and_writes_ack_report(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    calls: list[tuple[IntentId, PaperMutationArm]] = []
    result = PaperProtectiveMutationExecution(
        _plan(),
        PaperMutationExecutionResult(
            PaperMutationKey("a" * 64),
            PaperMutationExecutionState.ACKNOWLEDGED,
            None,
        ),
        (),
        OBSERVED_AT + dt.timedelta(seconds=1),
    )

    def open_session(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        return cast(AbstractContextManager[PaperOperatingSession], _FakeSession(result, calls))

    exit_code = cli.main(
        [
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(store.path),
            "--output-dir",
            str(tmp_path / "report"),
            "--intent-id",
            intent().intent_id,
        ],
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=open_session,
    )

    report = (tmp_path / "report" / "paper_protective_oco_smoke_ko.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert calls == [(intent().intent_id, PaperMutationArm(PAPER_MUTATION_ARM_VALUE))]
    assert "- 결과: acknowledged" in report
    assert f"parent_intent: {intent().intent_id}" in report
    assert "quantity: 10" in report


def test_protective_oco_smoke_reports_noop_without_broker_post(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)

    def open_session(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        return cast(
            AbstractContextManager[PaperOperatingSession],
            _FakeSession(NoProtectiveExitRequired(intent().intent_id), []),
        )

    exit_code = cli.main(
        [
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(store.path),
            "--output-dir",
            str(tmp_path / "report"),
            "--intent-id",
            intent().intent_id,
        ],
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=open_session,
    )

    report = (tmp_path / "report" / "paper_protective_oco_smoke_ko.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert "- 결과: no_protective_exit_required" in report


def test_protective_oco_smoke_reports_cancel_stage_as_redacted_incomplete(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    parent_intent = IntentId("sensitive-parent-intent")
    source_plan_key = ProtectiveOcoPlanKey("p" * 64)
    broker_order_id = BrokerOrderId("sensitive-broker-order")
    mutation_key = PaperMutationKey("m" * 64)
    result = PaperProtectiveCancelMutationExecution(
        ProtectiveOcoResizeCancelPlan(
            parent_intent,
            source_plan_key,
            broker_order_id,
            "AAA",
            OBSERVED_AT,
        ),
        PaperMutationExecutionResult(
            mutation_key,
            PaperMutationExecutionState.ACKNOWLEDGED,
            broker_order_id,
        ),
        (),
        OBSERVED_AT + dt.timedelta(seconds=1),
    )

    def open_session(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        return cast(
            AbstractContextManager[PaperOperatingSession],
            _FakeSession(result, []),
        )

    exit_code = cli.main(
        [
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(store.path),
            "--output-dir",
            str(tmp_path / "report"),
            "--intent-id",
            intent().intent_id,
        ],
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=open_session,
    )

    report = (tmp_path / "report" / "paper_protective_oco_smoke_ko.md").read_text(encoding="utf-8")
    assert exit_code == 2
    assert "- 결과: incomplete" in report
    assert "다음 current-epoch" in report
    for secret in (
        parent_intent,
        source_plan_key,
        broker_order_id,
        mutation_key,
    ):
        assert secret not in report


def test_protective_oco_smoke_blocks_on_current_epoch_reasons(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)

    def open_session(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        return cast(
            AbstractContextManager[PaperOperatingSession],
            _FakeSession(BlockedProtectiveExitPlan(("current-epoch 불일치",)), []),
        )

    exit_code = cli.main(
        [
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(store.path),
            "--output-dir",
            str(tmp_path / "report"),
            "--intent-id",
            intent().intent_id,
        ],
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=open_session,
    )

    report = (tmp_path / "report" / "paper_protective_oco_smoke_ko.md").read_text(encoding="utf-8")
    assert exit_code == 1
    assert "current-epoch 불일치" in report


def _plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        intent().intent_id,
        "AAA",
        PaperOrderSide.SELL,
        10,
        Decimal("10.5"),
        Decimal("9.75"),
    )
