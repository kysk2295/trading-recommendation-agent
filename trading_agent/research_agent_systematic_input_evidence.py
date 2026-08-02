from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from trading_agent.data_capability_models import DataUse
from trading_agent.data_foundation_manifest import DataFoundationManifest, load_data_foundation_artifact
from trading_agent.intraday_research_dataset_catalog_models import IntradayResearchDatasetCatalogReceipt
from trading_agent.intraday_research_dataset_models import IntradayResearchDatasetReceipt
from trading_agent.intraday_research_input_binding_models import IntradayResearchInputBindingReceipt
from trading_agent.kis_live import NEW_YORK
from trading_agent.replay import load_bounded_bar_source
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.strategy_data_gate import StrategyDataStatus

MAX_SYSTEMATIC_INPUT_SESSIONS: Final = 60
MAX_SYSTEMATIC_INPUT_BARS: Final = 100_000
MAX_SYSTEMATIC_INPUT_RSS_GIB: Final = 10.0
_MAX_ARTIFACT_BYTES: Final = 64 * 1024 * 1024
_SHA: Final = r"[0-9a-f]{64}"
_DATASET_CSV: Final = re.compile(rf"intraday_point_in_time_{_SHA}\.csv")
_DATASET_RECEIPT: Final = re.compile(rf"intraday_point_in_time_{_SHA}_{_SHA}\.json")
_CATALOG_RECEIPT: Final = re.compile(rf"intraday_research_catalog_{_SHA}\.json")
_BINDING_RECEIPT: Final = re.compile(rf"intraday_research_input_binding_{_SHA}\.json")
_FOUNDATION: Final = re.compile(rf"intraday_data_foundation_[a-z0-9][a-z0-9_]{{0,63}}_{_SHA}\.json")
_GRAPH_ARTIFACTS: Final = (
    (_DATASET_CSV, "*.csv"),
    (_DATASET_RECEIPT, "intraday_point_in_time_*.json"),
    (_CATALOG_RECEIPT, "intraday_research_catalog_*.json"),
    (_BINDING_RECEIPT, "intraday_research_input_binding_*.json"),
    (_FOUNDATION, "intraday_data_foundation_*.json"),
)


class SystematicInputEvidenceError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class VerifiedSystematicInputEvidence:
    input_csv_path: Path
    input_csv_sha256: str
    dataset_receipt_path: Path
    dataset_receipt_sha256: str
    catalog_receipt_path: Path
    catalog_receipt_sha256: str
    input_binding_receipt_path: Path
    input_binding_receipt_sha256: str
    foundation_path: Path
    foundation_sha256: str
    producer_commit_sha: str
    input_sha256: str
    selected_session_dates: tuple[dt.date, ...]
    bar_count: int
    max_sessions: int
    max_bars: int
    rss_limit_gib: float
    registered_at: dt.datetime


def verify_systematic_input_evidence_graph(
    artifact_root: Path,
) -> VerifiedSystematicInputEvidence:
    try:
        root = _require_artifact_root(artifact_root)
        paths = tuple(_one_artifact(root, pattern, glob) for pattern, glob in _GRAPH_ARTIFACTS)
        input_csv_path, dataset_path, catalog_path, binding_path, foundation_path = paths
        dataset, dataset_sha = _load_canonical_model(dataset_path, IntradayResearchDatasetReceipt)
        catalog, catalog_sha = _load_canonical_model(catalog_path, IntradayResearchDatasetCatalogReceipt)
        binding, binding_sha = _load_canonical_model(binding_path, IntradayResearchInputBindingReceipt)
        foundation, foundation_sha = _load_canonical_model(foundation_path, DataFoundationManifest)
        input_sha, bar_count, session_dates = _verify_dataset(input_csv_path, dataset, binding)
        eligible_dates = tuple(
            sorted(audit.session_date for audit in catalog.audits if audit.eligible and audit.session_date is not None)
        )
        if (
            dataset_path.name != f"intraday_point_in_time_{dataset.input_sha256}_{dataset_sha}.json"
            or catalog.dataset_input_sha256 != dataset.input_sha256
            or catalog.dataset_receipt_name != dataset_path.name
            or catalog.selected_session_dates != dataset.session_dates
            or catalog.selected_source_sha256s != dataset.source_session_sha256s
            or catalog.candidate_sessions != len(catalog.audits)
            or tuple(audit.session_name for audit in catalog.audits) != tuple(
                sorted({audit.session_name for audit in catalog.audits})
            )
            or len(eligible_dates) != len(set(eligible_dates))
            or catalog.selected_session_dates != eligible_dates[-len(catalog.selected_session_dates) :]
            or any(audit.eligible == bool(audit.reason_codes) for audit in catalog.audits)
            or not 1 <= catalog.minimum_sessions <= len(catalog.selected_session_dates)
            or not set(catalog.required_session_dates).issubset(catalog.selected_session_dates)
            or not set(catalog.selected_session_dates).issubset(eligible_dates)
            or any(re.fullmatch(_SHA, digest) is None for digest in dataset.source_session_sha256s)
        ):
            raise SystematicInputEvidenceError("catalog_edges_invalid")
        provenance_hashes = (
            binding.entitlement_contract_sha256,
            binding.source_queue_snapshot_id,
            binding.manifest_sha256,
        )
        if (
            binding.registered_at.tzinfo is None
            or binding.registered_at.utcoffset() is None
            or binding.input_sha256 != input_sha
            or binding.dataset_receipt_sha256 != dataset_sha
            or binding.dataset_producer_commit_sha != dataset.producer_commit_sha
            or binding.foundation_sha256s != (foundation_sha,)
            or len(binding.queue_card_keys) != 1
            or any(re.fullmatch(_SHA, digest) is None for digest in provenance_hashes)
        ):
            raise SystematicInputEvidenceError("binding_edges_invalid")
        artifact = load_data_foundation_artifact(foundation_path)
        lane = foundation.strategy_lane
        sources = (
            *(item.source_id for item in foundation.capabilities),
            *(item.source_id for item in foundation.entitlements),
            *(source for item in foundation.requirements for source in item.declared_source_ids),
        )
        if (
            artifact.sha256 != foundation_sha
            or artifact.manifest != foundation
            or foundation_path.name != f"intraday_data_foundation_{lane.strategy_id}_{foundation_sha}.json"
            or lane.market_id is not MarketId.US_EQUITIES
            or lane.agent_family is not AgentFamily.DAY_TRADING
            or foundation.evaluated_at > binding.registered_at
            or foundation.evaluate_data_readiness().status is not StrategyDataStatus.READY
            or any(source.provider == "fixture" for source in sources)
            or any(
                requirement.data_use is not DataUse.HISTORICAL_RESEARCH
                or requirement.market_domain is not DataMarketDomain.US_EQUITIES
                or requirement.event_type != "minute_bar"
                for requirement in foundation.requirements
            )
        ):
            raise SystematicInputEvidenceError("foundation_not_production_ready")
        return VerifiedSystematicInputEvidence(
            input_csv_path=input_csv_path,
            input_csv_sha256=input_sha,
            dataset_receipt_path=dataset_path,
            dataset_receipt_sha256=dataset_sha,
            catalog_receipt_path=catalog_path,
            catalog_receipt_sha256=catalog_sha,
            input_binding_receipt_path=binding_path,
            input_binding_receipt_sha256=binding_sha,
            foundation_path=foundation_path,
            foundation_sha256=foundation_sha,
            producer_commit_sha=dataset.producer_commit_sha,
            input_sha256=input_sha,
            selected_session_dates=session_dates,
            bar_count=bar_count,
            max_sessions=MAX_SYSTEMATIC_INPUT_SESSIONS,
            max_bars=MAX_SYSTEMATIC_INPUT_BARS,
            rss_limit_gib=MAX_SYSTEMATIC_INPUT_RSS_GIB,
            registered_at=binding.registered_at,
        )
    except SystematicInputEvidenceError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError):
        raise SystematicInputEvidenceError("invalid_or_tampered_graph") from None


def _require_artifact_root(path: Path) -> Path:
    if not path.is_absolute():
        raise SystematicInputEvidenceError("artifact_root_not_absolute")
    metadata = os.lstat(path)
    root = path.resolve(strict=True)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or path != root
        or _path_is_prohibited(root)
    ):
        raise SystematicInputEvidenceError("artifact_root_not_admissible")
    return root


def _one_artifact(root: Path, pattern: re.Pattern[str], glob: str) -> Path:
    matches = tuple(candidate for candidate in root.rglob(glob) if pattern.fullmatch(candidate.name) is not None)
    if len(matches) != 1:
        raise SystematicInputEvidenceError("evidence_graph_cardinality_invalid")
    candidate = matches[0]
    path = candidate.resolve(strict=True)
    if candidate != path or not path.is_relative_to(root) or _path_is_prohibited(path):
        raise SystematicInputEvidenceError("artifact_path_not_admissible")
    return path


def _read_private_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > _MAX_ARTIFACT_BYTES
        ):
            raise SystematicInputEvidenceError("artifact_not_private_immutable_file")
        payload = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, _MAX_ARTIFACT_BYTES + 1 - len(payload))):
            payload.extend(chunk)
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if len(payload) != before.st_size or before_identity != after_identity:
            raise SystematicInputEvidenceError("artifact_changed_during_verification")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _load_canonical_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> tuple[ModelT, str]:
    raw = _read_private_bytes(path)
    model = model_type.model_validate_json(raw)
    canonical = (json.dumps(model.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    if raw != canonical or not path.name.endswith(f"_{digest}.json"):
        raise SystematicInputEvidenceError("artifact_not_canonical")
    return model, digest


def _verify_dataset(
    path: Path,
    receipt: IntradayResearchDatasetReceipt,
    binding: IntradayResearchInputBindingReceipt,
) -> tuple[str, int, tuple[dt.date, ...]]:
    raw = _read_private_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    if path.name != f"intraday_point_in_time_{receipt.input_sha256}.csv" or digest != receipt.input_sha256:
        raise SystematicInputEvidenceError("dataset_identity_invalid")
    source = load_bounded_bar_source(
        path,
        max_rows=MAX_SYSTEMATIC_INPUT_BARS,
        max_sessions=MAX_SYSTEMATIC_INPUT_SESSIONS,
    )
    dates = tuple(sorted({bar.timestamp.astimezone(NEW_YORK).date() for bar in source.bars}))
    if (
        source.sha256 != digest
        or len(source.bars) != receipt.bar_count
        or dates != receipt.session_dates
        or len(receipt.source_session_sha256s) != len(receipt.session_dates)
        or len(dates) > MAX_SYSTEMATIC_INPUT_SESSIONS
        or len(source.bars) > MAX_SYSTEMATIC_INPUT_BARS
        or max(bar.timestamp for bar in source.bars) > binding.registered_at
    ):
        raise SystematicInputEvidenceError("dataset_content_invalid")
    return digest, len(source.bars), dates


def _path_is_prohibited(path: Path) -> bool:
    parts = path.parts
    return "examples" in parts or any(
        first == "outputs" and second == "live_sessions"
        for first, second in pairwise(parts)
    )
