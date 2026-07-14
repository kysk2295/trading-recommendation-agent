from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.paper_mutation_executor_models import PaperMutationExecutionResult
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryResult
from trading_agent.paper_protective_oco_models import ProtectiveOcoExitPlan
from trading_agent.paper_safety_models import PaperSafetyPlan


@dataclass(frozen=True, slots=True)
class PaperSafetyMutationExecution:
    plan: PaperSafetyPlan
    results: tuple[PaperMutationExecutionResult, ...]
    recoveries: tuple[PaperMutationRecoveryResult, ...]
    reconciled_at: dt.datetime


@dataclass(frozen=True, slots=True)
class PaperProtectiveMutationExecution:
    plan: ProtectiveOcoExitPlan
    result: PaperMutationExecutionResult
    recoveries: tuple[PaperMutationRecoveryResult, ...]
    reconciled_at: dt.datetime
