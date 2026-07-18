from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer

from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.research_evidence_artifact import write_research_evidence_artifact
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.us_broad_scanner_foundation import build_us_broad_scanner_foundation
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerProjectionError
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_scanner_research_projection import (
    project_us_scanner_research_evidence,
)
from trading_agent.us_subscription_models import BroadScannerSnapshot


@dataclass(frozen=True, slots=True)
class ResearchProjectionOptions:
    foundation_manifest: str | None
    store: str | None
    canonical_root: str | None
    security_master_store: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchProjectionConfig:
    foundation_manifest: Path | None
    store: Path
    canonical_root: Path
    evidence_artifact_root: Path
    security_master_store: Path | None = None


def configure_research_projection(
    options: ResearchProjectionOptions,
) -> ResearchProjectionConfig | None:
    values = (
        options.foundation_manifest,
        options.store,
        options.canonical_root,
        options.security_master_store,
    )
    if all(value is None for value in values):
        return None
    if options.store is None or options.canonical_root is None:
        raise typer.BadParameter("research projection requires store and canonical root")
    if (options.foundation_manifest is None) == (options.security_master_store is None):
        raise typer.BadParameter("research foundation manifest and security master store are mutually exclusive")
    return ResearchProjectionConfig(
        None if options.foundation_manifest is None else Path(options.foundation_manifest),
        Path(options.store),
        Path(options.canonical_root),
        Path(options.store).parent / "research-evidence",
        None if options.security_master_store is None else Path(options.security_master_store),
    )


def project_opportunity_research_input(
    opportunity: OpportunitySnapshot | None,
    config: ResearchProjectionConfig | None,
) -> BroadScannerSnapshot | None:
    if opportunity is None or config is None:
        return None
    security_master = (
        None
        if config.security_master_store is None
        else AlpacaSecurityMasterStore(config.security_master_store).latest_snapshot()
    )
    if config.security_master_store is not None and security_master is None:
        raise UsOpportunityScannerProjectionError
    foundation = (
        build_us_broad_scanner_foundation(opportunity, security_master)
        if security_master is not None
        else load_data_foundation_manifest(_required_foundation_path(config))
    )
    snapshot = UsOpportunityScannerProjector(
        UsOpportunityScannerStore(config.store),
        config.canonical_root,
    ).project(opportunity, foundation, security_master=security_master)
    model = project_us_scanner_research_evidence(config.store)
    _ = write_research_evidence_artifact(config.evidence_artifact_root, model)
    return snapshot


def _required_foundation_path(config: ResearchProjectionConfig) -> Path:
    if config.foundation_manifest is None:
        raise UsOpportunityScannerProjectionError
    return config.foundation_manifest


__all__ = (
    "ResearchProjectionConfig",
    "ResearchProjectionOptions",
    "configure_research_projection",
    "project_opportunity_research_input",
)
