from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from trading_agent.hermes_delivery_projection import (
    HermesProjectionResult,
    HermesProjectionSources,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_session_delivery_projection import (
    project_us_session_contract_outboxes,
)
from trading_agent.us_session_delivery_reconciliation import (
    UsSessionDeliveryReconciliation,
    UsSessionDeliveryReconciliationRequest,
    reconcile_us_session_deliveries,
    write_us_session_delivery_reconciliation,
)
from trading_agent.us_session_delivery_terminal import (
    UsSessionDeliveryTerminalRequest,
    UsSessionDeliveryTerminalResult,
    project_us_session_delivery_terminal,
)
from trading_agent.us_session_delivery_terminal_artifact import (
    read_us_session_delivery_terminal,
    write_us_session_delivery_terminal,
)


@dataclass(frozen=True, slots=True)
class ProjectUsSessionCommand:
    sources: HermesProjectionSources
    session_date: dt.date


@dataclass(frozen=True, slots=True)
class FinalizeUsSessionCommand:
    sources: HermesProjectionSources
    session_date: dt.date
    evaluated_at: dt.datetime
    output: Path


@dataclass(frozen=True, slots=True)
class ReconcileUsSessionCommand:
    sources: HermesProjectionSources
    session_date: dt.date
    generated_at: dt.datetime
    output: Path
    terminal_artifact: Path | None = None


def project_us_session_command(
    command: ProjectUsSessionCommand,
    store: HermesDeliveryStore,
) -> HermesProjectionResult:
    with store.writer() as writer:
        return project_us_session_contract_outboxes(
            command.sources,
            command.session_date,
            writer,
        )


def finalize_us_session_command(
    command: FinalizeUsSessionCommand,
    store: HermesDeliveryStore,
) -> UsSessionDeliveryTerminalResult:
    result = project_us_session_delivery_terminal(
        UsSessionDeliveryTerminalRequest(
            sources=command.sources,
            session_date=command.session_date,
            evaluated_at=command.evaluated_at,
        ),
        store,
    )
    write_us_session_delivery_terminal(command.output, result.artifact)
    return result


def reconcile_us_session_command(
    command: ReconcileUsSessionCommand,
    store: HermesDeliveryStore,
) -> UsSessionDeliveryReconciliation:
    report = reconcile_us_session_deliveries(
        UsSessionDeliveryReconciliationRequest(
            sources=command.sources,
            session_date=command.session_date,
            generated_at=command.generated_at,
            terminal_artifact=(
                None
                if command.terminal_artifact is None
                else read_us_session_delivery_terminal(command.terminal_artifact)
            ),
        ),
        store,
    )
    write_us_session_delivery_reconciliation(command.output, report)
    return report
