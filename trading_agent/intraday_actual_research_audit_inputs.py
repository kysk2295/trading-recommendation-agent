from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.data_foundation_manifest import (
    InvalidDataFoundationManifestError,
    load_data_foundation_artifact,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditError,
)
from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchRunPlan,
)
from trading_agent.intraday_research_data_gate import (
    InvalidIntradayResearchDataError,
    require_intraday_research_data,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogReceipt,
)
from trading_agent.intraday_research_dataset_models import (
    IntradayResearchDatasetReceipt,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingReceipt,
)
from trading_agent.intraday_research_loop_models import (
    IntradayResearchManifest,
    InvalidIntradayResearchManifestError,
    load_intraday_research_manifest,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    read_private_text,
)
from trading_agent.replay import BoundedReplaySourceError, load_bounded_bar_source


@dataclass(frozen=True, slots=True)
class AuditedDatasetEvidence:
    input_sha256: str
    receipt_sha256: str
    producer_commit_sha: str


@dataclass(frozen=True, slots=True)
class AuditedBindingEvidence:
    manifest: IntradayResearchManifest
    manifest_sha256: str
    foundation_sha256s: tuple[str, ...]


def load_actual_research_inputs(
    plan: IntradayActualResearchRunPlan,
) -> tuple[AuditedDatasetEvidence, AuditedBindingEvidence]:
    try:
        dataset = _load_dataset(plan)
        return dataset, _load_binding(plan, dataset)
    except IntradayActualResearchAuditError:
        raise
    except (
        BoundedReplaySourceError,
        InvalidDataFoundationManifestError,
        InvalidIntradayResearchDataError,
        InvalidIntradayResearchManifestError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayActualResearchAuditError("invalid_input_evidence") from None


def _load_dataset(plan: IntradayActualResearchRunPlan) -> AuditedDatasetEvidence:
    spec = plan.content.spec
    root = spec.paths.dataset_root
    catalog_path = _single_path(root, "intraday_research_catalog_*.json")
    catalog_raw = read_private_text(catalog_path)
    catalog = IntradayResearchDatasetCatalogReceipt.model_validate_json(catalog_raw)
    if (
        catalog_path.name != f"intraday_research_catalog_{_sha(catalog_raw)}.json"
        or catalog.required_session_dates != spec.required_session_dates
        or not set(spec.required_session_dates).issubset(catalog.selected_session_dates)
    ):
        raise IntradayActualResearchAuditError("dataset_catalog_mismatch")
    receipt_path = root / catalog.dataset_receipt_name
    receipt_raw = read_private_text(receipt_path)
    receipt_sha = _sha(receipt_raw)
    receipt = IntradayResearchDatasetReceipt.model_validate_json(receipt_raw)
    source = load_bounded_bar_source(
        root / f"intraday_point_in_time_{receipt.input_sha256}.csv",
        max_rows=spec.max_bars,
        max_sessions=spec.max_sessions,
    )
    if (
        receipt_path.name
        != f"intraday_point_in_time_{receipt.input_sha256}_{receipt_sha}.json"
        or receipt.input_sha256 != catalog.dataset_input_sha256
        or receipt.producer_commit_sha != spec.dataset_producer_commit_sha
        or receipt.session_dates != catalog.selected_session_dates
        or source.sha256 != receipt.input_sha256
        or len(source.bars) != receipt.bar_count
    ):
        raise IntradayActualResearchAuditError("dataset_evidence_mismatch")
    return AuditedDatasetEvidence(
        input_sha256=receipt.input_sha256,
        receipt_sha256=receipt_sha,
        producer_commit_sha=receipt.producer_commit_sha,
    )


def _load_binding(
    plan: IntradayActualResearchRunPlan,
    dataset: AuditedDatasetEvidence,
) -> AuditedBindingEvidence:
    spec = plan.content.spec
    root = spec.paths.binding_root
    binding_path = _single_path(root, "intraday_research_input_binding_*.json")
    binding_raw = read_private_text(binding_path)
    receipt = IntradayResearchInputBindingReceipt.model_validate_json(binding_raw)
    manifest_path = _single_path(root, "intraday_research_manifest_*.json")
    manifest_raw = read_private_text(manifest_path)
    manifest_sha = _sha(manifest_raw)
    manifest = load_intraday_research_manifest(manifest_path)
    foundation_paths = tuple(sorted(root.glob("intraday_data_foundation_*.json")))
    observed_foundation_sha256s = tuple(
        load_data_foundation_artifact(path).sha256 for path in foundation_paths
    )
    foundation_sha256s = _declared_foundation_sha256s(manifest)
    require_intraday_research_data(manifest, foundation_paths)
    expected_bindings = tuple(
        (item.strategy, item.strategy_version, item.queue_card_key)
        for item in spec.strategy_bindings
    )
    actual_bindings = tuple(
        (item.strategy, item.strategy_version, item.queue_card_key)
        for item in manifest.hypotheses
    )
    if (
        binding_path.name
        != f"intraday_research_input_binding_{_sha(binding_raw)}.json"
        or manifest_path.name != f"intraday_research_manifest_{manifest_sha}.json"
        or manifest.schema_version != 2
        or manifest.input_sha256 != dataset.input_sha256
        or manifest.code_version != spec.code_version
        or manifest.source_queue_snapshot_id != plan.content.source_queue_snapshot_id
        or expected_bindings != actual_bindings
        or set(observed_foundation_sha256s) != set(foundation_sha256s)
        or receipt.input_sha256 != dataset.input_sha256
        or receipt.dataset_receipt_sha256 != dataset.receipt_sha256
        or receipt.dataset_producer_commit_sha != dataset.producer_commit_sha
        or receipt.manifest_sha256 != manifest_sha
        or receipt.foundation_sha256s != foundation_sha256s
    ):
        raise IntradayActualResearchAuditError("binding_evidence_mismatch")
    return AuditedBindingEvidence(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        foundation_sha256s=foundation_sha256s,
    )


def _declared_foundation_sha256s(
    manifest: IntradayResearchManifest,
) -> tuple[str, ...]:
    values = tuple(
        value
        for item in manifest.hypotheses
        if (value := item.data_foundation_sha256) is not None
    )
    if len(values) != len(manifest.hypotheses):
        raise IntradayActualResearchAuditError("foundation_identity_missing")
    return values


def _single_path(root: Path, pattern: str) -> Path:
    paths = tuple(sorted(root.glob(pattern)))
    if len(paths) != 1:
        raise IntradayActualResearchAuditError("artifact_cardinality_mismatch")
    return paths[0]


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = (
    "AuditedBindingEvidence",
    "AuditedDatasetEvidence",
    "load_actual_research_inputs",
)
