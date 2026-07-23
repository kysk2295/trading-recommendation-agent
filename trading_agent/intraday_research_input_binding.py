from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trading_agent.data_capability_models import DataEntitlement, DataUse
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.intraday_research_dataset_models import IntradayResearchDatasetReceipt
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingError,
    IntradayResearchInputBindingReceipt,
    IntradayResearchInputBindingRequest,
    IntradayResearchInputBindingResult,
)
from trading_agent.intraday_research_input_foundation import (
    build_actual_intraday_data_foundation,
)
from trading_agent.intraday_research_loop_models import (
    IntradayHypothesisSelection,
    IntradayResearchManifest,
)
from trading_agent.kis_live import NEW_YORK
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.replay import BoundedReplaySourceError, load_bounded_bar_source
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.source_driven_hypothesis_queue import (
    load_source_driven_hypothesis_queue,
)
from trading_agent.source_driven_hypothesis_queue_models import (
    HypothesisQueueRoute,
    InvalidSourceDrivenHypothesisQueueError,
)


def bind_intraday_research_input(
    request: IntradayResearchInputBindingRequest,
) -> IntradayResearchInputBindingResult:
    try:
        if (
            request.observed_at.tzinfo is None
            or request.observed_at.utcoffset() is None
            or request.registered_at > request.observed_at
        ):
            raise IntradayResearchInputBindingError("registration_time_invalid")
        receipt, receipt_sha = _load_dataset_receipt(request.dataset_receipt)
        source = _load_dataset(request, receipt)
        entitlement_raw = read_private_text(request.entitlement_contract)
        entitlement = DataEntitlement.model_validate_json(entitlement_raw)
        if (
            entitlement.source_id.provider == "fixture"
            or DataMarketDomain.US_EQUITIES not in entitlement.market_domains
            or "minute_bar" not in entitlement.event_types
            or DataUse.HISTORICAL_RESEARCH not in entitlement.permitted_uses
        ):
            raise IntradayResearchInputBindingError("entitlement_not_actual_research")
        queue = load_source_driven_hypothesis_queue(request.source_queue_artifact)
        selections, foundations = _build_selections_and_foundations(request, receipt, entitlement, queue)
        manifest = IntradayResearchManifest(
            schema_version=2,
            family="source_backed_intraday_challengers_v2",
            code_version=request.code_version,
            hypotheses=selections,
            source_queue_snapshot_id=queue.snapshot_id,
            input_sha256=source.sha256,
            registered_at=request.registered_at,
            evaluator_version="intraday_walk_forward_v1",
            minimum_training_sessions=request.minimum_training_sessions,
            max_bars=request.max_bars,
            max_sessions=request.max_sessions,
            per_side_fee_bps=request.per_side_fee_bps,
            per_side_slippage_bps=request.per_side_slippage_bps,
            bootstrap_samples=request.bootstrap_samples,
            rss_limit_gib=request.rss_limit_gib,
        )
        foundation_payloads = tuple(_payload(item) for item in foundations)
        foundation_hashes = tuple(_sha(payload) for payload in foundation_payloads)
        if tuple(item.data_foundation_sha256 for item in selections) != foundation_hashes:
            raise IntradayResearchInputBindingError("foundation_hash_mismatch")
        manifest_payload = _payload(manifest)
        manifest_hash = _sha(manifest_payload)
        binding_receipt = IntradayResearchInputBindingReceipt(
            input_sha256=source.sha256,
            dataset_receipt_sha256=receipt_sha,
            entitlement_contract_sha256=_sha(entitlement_raw),
            source_queue_snapshot_id=queue.snapshot_id,
            queue_card_keys=tuple(item.queue_card_key or "" for item in selections),
            foundation_sha256s=foundation_hashes,
            manifest_sha256=manifest_hash,
            registered_at=request.registered_at,
        )
        binding_payload = _payload(binding_receipt)
        foundation_paths = tuple(
            request.output_root / f"intraday_data_foundation_{item.strategy_lane.strategy_id}_{digest}.json"
            for item, digest in zip(foundations, foundation_hashes, strict=True)
        )
        manifest_path = request.output_root / f"intraday_research_manifest_{manifest_hash}.json"
        binding_path = request.output_root / f"intraday_research_input_binding_{_sha(binding_payload)}.json"
        created = any(
            (
                *(
                    publish_private_immutable_text(path, payload)
                    for path, payload in zip(foundation_paths, foundation_payloads, strict=True)
                ),
                publish_private_immutable_text(manifest_path, manifest_payload),
                publish_private_immutable_text(binding_path, binding_payload),
            )
        )
        return IntradayResearchInputBindingResult(
            input_sha256=source.sha256,
            foundation_paths=foundation_paths,
            foundation_sha256s=foundation_hashes,
            manifest_path=manifest_path,
            manifest_sha256=manifest_hash,
            receipt_path=binding_path,
            created=created,
        )
    except IntradayResearchInputBindingError:
        raise
    except (
        BoundedReplaySourceError,
        InvalidPrivateImmutableFileError,
        InvalidSourceDrivenHypothesisQueueError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayResearchInputBindingError("invalid_evidence_or_contract") from None


def _load_dataset_receipt(path: Path) -> tuple[IntradayResearchDatasetReceipt, str]:
    raw = read_private_text(path)
    receipt = IntradayResearchDatasetReceipt.model_validate_json(raw)
    digest = _sha(raw)
    expected_name = f"intraday_point_in_time_{receipt.input_sha256}_{digest}.json"
    if path.name != expected_name or raw != _payload(receipt):
        raise IntradayResearchInputBindingError("dataset_receipt_not_canonical")
    return receipt, digest


def _load_dataset(
    request: IntradayResearchInputBindingRequest,
    receipt: IntradayResearchDatasetReceipt,
):
    raw = read_private_text(request.dataset_csv)
    if (
        request.dataset_csv.name != f"intraday_point_in_time_{receipt.input_sha256}.csv"
        or _sha(raw) != receipt.input_sha256
        or len(receipt.session_dates) > request.max_sessions
        or len(receipt.source_session_sha256s) != len(receipt.session_dates)
    ):
        raise IntradayResearchInputBindingError("dataset_not_bound_to_receipt")
    source = load_bounded_bar_source(
        request.dataset_csv,
        max_rows=request.max_bars,
        max_sessions=request.max_sessions,
    )
    observed_dates = {bar.timestamp.astimezone(NEW_YORK).date() for bar in source.bars}
    if (
        source.sha256 != receipt.input_sha256
        or len(source.bars) != receipt.bar_count
        or not observed_dates.issubset(set(receipt.session_dates))
        or max(bar.timestamp for bar in source.bars) > request.registered_at
    ):
        raise IntradayResearchInputBindingError("dataset_content_invalid")
    return source


def _build_selections_and_foundations(request, receipt, entitlement, queue):
    if (
        not request.strategy_bindings
        or len(request.strategy_bindings) > 3
        or request.registered_at < queue.snapshot.as_of
    ):
        raise IntradayResearchInputBindingError("binding_budget_or_time_invalid")
    items = {item.card_key: item for item in queue.snapshot.items}
    foundations: list[DataFoundationManifest] = []
    selections: list[IntradayHypothesisSelection] = []
    for binding in request.strategy_bindings:
        item = items.get(binding.queue_card_key)
        if item is None or not _queue_item_accepts_binding(item, binding.strategy_version):
            raise IntradayResearchInputBindingError("queue_card_not_design_ready")
        foundation = build_actual_intraday_data_foundation(
            binding.strategy,
            request.registered_at,
            receipt,
            entitlement,
        )
        foundation_hash = _sha(_payload(foundation))
        foundations.append(foundation)
        selections.append(
            IntradayHypothesisSelection(
                strategy=binding.strategy,
                hypothesis_id=item.hypothesis_id,
                strategy_version=binding.strategy_version,
                queue_card_key=item.card_key,
                data_foundation_sha256=foundation_hash,
            )
        )
    return tuple(selections), tuple(foundations)


def _queue_item_accepts_binding(item, strategy_version: str) -> bool:
    if item.route is HypothesisQueueRoute.STRATEGY_DESIGN:
        return not item.strategy_versions and not item.historical_trial_ids
    return (
        item.route
        in {
            HypothesisQueueRoute.HISTORICAL_REPLAY,
            HypothesisQueueRoute.INDEPENDENT_REVIEW,
            HypothesisQueueRoute.RECOVERY,
        }
        and item.strategy_versions == (strategy_version,)
    )


def _payload(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()
