from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from tests.challenger_replay_fixtures import write_closed_source_session
from tests.intraday_research_input_binding_fixtures import (
    NOW,
    PRODUCER_COMMIT,
    write_entitlement,
    write_queue,
)
from trading_agent.intraday_research_dataset_catalog import (
    materialize_intraday_research_dataset_catalog,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogRequest,
)
from trading_agent.intraday_research_input_binding import bind_intraday_research_input
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingRequest,
    IntradayResearchStrategyBinding,
)
from trading_agent.research_agent_systematic_input_evidence import (
    verify_systematic_input_evidence_graph,
)
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    write_systematic_input_activation,
)
from trading_agent.strategy_factory import StrategyMode


@dataclass(frozen=True, slots=True)
class SystematicInputGraphFixture:
    root: Path
    input_csv_path: Path
    dataset_receipt_path: Path
    catalog_receipt_path: Path
    input_binding_receipt_path: Path
    foundation_path: Path


@dataclass(frozen=True, slots=True)
class ReadySystematicInputFixture:
    graph: SystematicInputGraphFixture
    activation_path: Path
    activation: ReadySystematicInputActivation


def write_systematic_input_graph(root: Path) -> SystematicInputGraphFixture:
    source = root / "strict-source"
    write_closed_source_session(
        source,
        include_censored_symbol=False,
        session_date=dt.date(2026, 7, 14),
    )
    catalog = materialize_intraday_research_dataset_catalog(
        IntradayResearchDatasetCatalogRequest(
            session_dirs=(source,),
            output_root=root / "catalog",
            minimum_sessions=1,
            max_sessions=60,
            max_bars=100_000,
            producer_commit_sha=PRODUCER_COMMIT,
        )
    )
    queue_path, card_keys = write_queue(root)
    entitlement_path = write_entitlement(root)
    binding = bind_intraday_research_input(
        IntradayResearchInputBindingRequest(
            dataset_csv=catalog.dataset.csv_path,
            dataset_receipt=catalog.dataset.receipt_path,
            entitlement_contract=entitlement_path,
            source_queue_artifact=queue_path,
            output_root=root / "binding",
            strategy_bindings=(
                IntradayResearchStrategyBinding(
                    strategy=StrategyMode.VWAP_RECLAIM,
                    strategy_version="actual_vwap_reclaim_v1",
                    queue_card_key=card_keys[0],
                ),
            ),
            code_version="e" * 40,
            registered_at=NOW,
            observed_at=NOW,
            minimum_training_sessions=0,
            max_bars=100_000,
            max_sessions=60,
            per_side_fee_bps=5,
            per_side_slippage_bps=15,
            bootstrap_samples=200,
            rss_limit_gib=9.5,
        )
    )
    return SystematicInputGraphFixture(
        root=root,
        input_csv_path=catalog.dataset.csv_path,
        dataset_receipt_path=catalog.dataset.receipt_path,
        catalog_receipt_path=catalog.catalog_receipt_path,
        input_binding_receipt_path=binding.receipt_path,
        foundation_path=binding.foundation_paths[0],
    )


def write_ready_systematic_input_activation(
    root: Path,
    activation_path: Path,
) -> ReadySystematicInputFixture:
    graph = write_systematic_input_graph(root)
    facts = verify_systematic_input_evidence_graph(graph.root)
    activation = ReadySystematicInputActivation(
        input_csv_path=facts.input_csv_path,
        input_csv_sha256=facts.input_csv_sha256,
        dataset_receipt_path=facts.dataset_receipt_path,
        dataset_receipt_sha256=facts.dataset_receipt_sha256,
        catalog_receipt_path=facts.catalog_receipt_path,
        catalog_receipt_sha256=facts.catalog_receipt_sha256,
        input_binding_receipt_path=facts.input_binding_receipt_path,
        input_binding_receipt_sha256=facts.input_binding_receipt_sha256,
        foundation_path=facts.foundation_path,
        foundation_sha256=facts.foundation_sha256,
        producer_commit_sha=facts.producer_commit_sha,
        input_sha256=facts.input_sha256,
        selected_session_dates=facts.selected_session_dates,
        bar_count=facts.bar_count,
        max_sessions=facts.max_sessions,
        max_bars=facts.max_bars,
        rss_limit_gib=facts.rss_limit_gib,
        activated_at=facts.registered_at,
    )
    write_systematic_input_activation(activation_path, activation)
    return ReadySystematicInputFixture(
        graph=graph,
        activation_path=activation_path,
        activation=activation,
    )


def write_blocked_systematic_input_activation(activation_path: Path) -> None:
    write_systematic_input_activation(
        activation_path,
        BlockedSystematicInputActivation(
            reason_code="production_input_unavailable",
            attempted_at=NOW,
        ),
    )


def replace_model_artifact(path: Path, model: BaseModel, prefix: str) -> tuple[Path, str]:
    payload = canonical_model_payload(model)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    replacement = path.with_name(f"{prefix}{digest}.json")
    path.unlink()
    replacement.write_text(payload, encoding="utf-8")
    replacement.chmod(0o600)
    return replacement, digest


def canonical_model_payload(model: BaseModel) -> str:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


__all__ = (
    "ReadySystematicInputFixture",
    "SystematicInputGraphFixture",
    "canonical_model_payload",
    "replace_model_artifact",
    "write_blocked_systematic_input_activation",
    "write_ready_systematic_input_activation",
    "write_systematic_input_graph",
)
