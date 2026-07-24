from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tests.us_day_operating_fixtures import NaturalPaperSession, admission
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryResult
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.us_day_session_inspection import DefaultUsDayReadOnlyOperations


class MutationRejectingPaperSession(NaturalPaperSession):
    def recover_mutations(self) -> tuple[PaperMutationRecoveryResult, ...]:
        raise AssertionError("read-only inspection must not call mutation recovery")


def test_preflight_and_recover_never_invoke_mutation_recovery(tmp_path: Path) -> None:
    order_admission = admission()
    session = MutationRejectingPaperSession(order_admission)

    @contextmanager
    def opener(_: AlpacaPaperCredentials, __: ExecutionStore) -> Iterator[PaperOperatingSession]:
        yield session

    operations = DefaultUsDayReadOnlyOperations(
        credentials_loader=lambda: AlpacaPaperCredentials("paper-key", "paper-secret"),
        session_opener=opener,
    )

    preflight = operations.preflight(tmp_path / "execution.sqlite3", order_admission)
    recovered = operations.recover(tmp_path / "execution.sqlite3")

    assert preflight.admission_approved is True
    assert preflight.reasons == ()
    assert recovered.reconciliation_passed is True


def test_recover_does_not_claim_broker_shadow_equality_without_shadow_evidence(
    tmp_path: Path,
) -> None:
    # Given: broker/local reconciliation is ready but no shadow ledger enters the API.
    order_admission = admission()
    session = MutationRejectingPaperSession(order_admission)

    @contextmanager
    def opener(_: AlpacaPaperCredentials, __: ExecutionStore) -> Iterator[PaperOperatingSession]:
        yield session

    operations = DefaultUsDayReadOnlyOperations(
        credentials_loader=lambda: AlpacaPaperCredentials("paper-key", "paper-secret"),
        session_opener=opener,
    )

    # When: the production read-only recovery projects the session inspection.
    recovered = operations.recover(tmp_path / "execution.sqlite3")

    # Then: only reconciliation passes; broker/shadow equality remains unproven.
    assert recovered.reconciliation_passed is True
    assert recovered.broker_shadow_ledger_equal is False
