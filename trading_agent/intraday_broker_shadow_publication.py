from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.adaptive_evaluation_source import load_evaluation_source
from trading_agent.execution_store import ExecutionStore
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_broker_shadow_evidence import build_broker_shadow_evidence
from trading_agent.intraday_broker_shadow_models import (
    BrokerShadowEvidenceArtifact,
    BrokerShadowEvidenceRequest,
    InvalidBrokerShadowEvidenceError,
)
from trading_agent.metrics import PaperTrade
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


@dataclass(frozen=True, slots=True)
class BrokerShadowPublicationRequest:
    current_session: Path
    execution_ledger: Path
    artifact_root: Path
    reviewed_at: dt.datetime


def publish_broker_shadow_evidence(
    request: BrokerShadowPublicationRequest,
) -> tuple[BrokerShadowEvidenceArtifact, bool]:
    try:
        source = load_evaluation_source(request.current_session)
        trades = tuple(
            trade
            for session in source.sessions
            for trade in session.trades
        )
        store = ExecutionStore(request.execution_ledger)
        if not store.is_initialized():
            raise InvalidBrokerShadowEvidenceError
        before = store.ledger_snapshot_identity()
        ledger = store.reconciliation_ledger()
        activities = store.paper_account_activities()
        protective_ocos = store.paper_recovery_protective_ocos()
        after = store.ledger_snapshot_identity()
        if before != after:
            raise InvalidBrokerShadowEvidenceError
        payload = build_broker_shadow_evidence(
            BrokerShadowEvidenceRequest(
                strategy_version=source.context.strategy_version,
                execution_snapshot_sha256=before.sha256,
                shadow_source_sha256=_shadow_source_sha256(trades),
                shadow_trades=trades,
                ledger=ledger,
                account_activities=activities,
                protective_oco_snapshots=protective_ocos,
                reviewed_at=request.reviewed_at,
            )
        )
        artifact = BrokerShadowEvidenceArtifact(
            artifact_id=hashlib.sha256(
                canonical_experiment_ledger_json(payload).encode()
            ).hexdigest(),
            payload=payload,
        )
        created = publish_private_immutable_text(
            request.artifact_root
            / f"intraday_broker_shadow_evidence_{artifact.artifact_id}.json",
            canonical_experiment_ledger_json(artifact) + "\n",
        )
        return artifact, created
    except InvalidBrokerShadowEvidenceError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        OSError,
        RuntimeError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidBrokerShadowEvidenceError from None


def load_broker_shadow_evidence_artifact(
    path: Path,
) -> BrokerShadowEvidenceArtifact:
    try:
        encoded = read_private_text(path)
        artifact = BrokerShadowEvidenceArtifact.model_validate_json(encoded)
        if (
            path.name
            != f"intraday_broker_shadow_evidence_{artifact.artifact_id}.json"
            or encoded != canonical_experiment_ledger_json(artifact) + "\n"
        ):
            raise InvalidBrokerShadowEvidenceError
        return artifact
    except InvalidBrokerShadowEvidenceError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidBrokerShadowEvidenceError from None


def _shadow_source_sha256(trades: tuple[PaperTrade, ...]) -> str:
    rows = tuple(
        {
            "recommendation_id": trade.recommendation_id,
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "entry_at": trade.entry_at.isoformat(),
            "exit_at": trade.exit_at.isoformat(),
            "entry": trade.entry,
            "exit": trade.exit,
            "gross_return": trade.gross_return,
            "exit_state": trade.exit_state.value,
            "uses_close_fallback": trade.uses_close_fallback,
        }
        for trade in sorted(
            trades,
            key=lambda item: (item.exit_at, item.recommendation_id),
        )
    )
    canonical = json.dumps(
        rows,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = (
    "BrokerShadowPublicationRequest",
    "load_broker_shadow_evidence_artifact",
    "publish_broker_shadow_evidence",
)
