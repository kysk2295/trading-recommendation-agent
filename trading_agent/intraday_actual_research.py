from __future__ import annotations

from trading_agent.intraday_actual_research_models import (
    IntradayActualResearchRequest,
    IntradayActualResearchResult,
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
)
from trading_agent.intraday_research_loop import (
    IntradayResearchLoopPaths,
    run_intraday_research_loop,
)
from trading_agent.intraday_research_loop_models import load_intraday_research_manifest


def run_intraday_actual_research(
    request: IntradayActualResearchRequest,
) -> IntradayActualResearchResult:
    paths = request.paths
    catalog = materialize_intraday_research_dataset_catalog(
        IntradayResearchDatasetCatalogRequest(
            session_dirs=request.session_dirs,
            output_root=paths.dataset_root,
            minimum_sessions=request.minimum_clean_sessions,
            max_sessions=request.max_sessions,
            max_bars=request.max_bars,
            producer_commit_sha=request.dataset_producer_commit_sha,
            required_session_dates=request.required_session_dates,
        )
    )
    dataset = catalog.dataset
    binding = bind_intraday_research_input(
        IntradayResearchInputBindingRequest(
            dataset_csv=dataset.csv_path,
            dataset_receipt=dataset.receipt_path,
            entitlement_contract=paths.entitlement_contract,
            source_queue_artifact=paths.source_queue_artifact,
            output_root=paths.binding_root,
            strategy_bindings=request.strategy_bindings,
            code_version=request.code_version,
            registered_at=request.registered_at,
            observed_at=request.observed_at,
            minimum_training_sessions=request.minimum_training_sessions,
            max_bars=request.max_bars,
            max_sessions=request.max_sessions,
            per_side_fee_bps=request.per_side_fee_bps,
            per_side_slippage_bps=request.per_side_slippage_bps,
            bootstrap_samples=request.bootstrap_samples,
            rss_limit_gib=request.rss_limit_gib,
        )
    )
    manifest = load_intraday_research_manifest(binding.manifest_path)
    loop = run_intraday_research_loop(
        manifest,
        IntradayResearchLoopPaths(
            input_csv=dataset.csv_path,
            lane_registry=paths.lane_registry,
            experiment_ledger=paths.experiment_ledger,
            artifact_root=paths.artifact_root,
            review_root=paths.review_root,
            source_queue_artifact=paths.source_queue_artifact,
            data_foundation_manifests=binding.foundation_paths,
            persisted_manifest_sha256=binding.manifest_sha256,
            required_outcome_trace_schema_version=(
                request.required_outcome_trace_schema_version
            ),
        ),
    )
    return IntradayActualResearchResult(catalog=catalog, binding=binding, loop=loop)


__all__ = ("run_intraday_actual_research",)
