from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsOpportunityEvidenceBundle,
    AlpacaNewsOpportunityEvidenceError,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


def publish_alpaca_news_opportunity_evidence(
    root: Path,
    bundle: AlpacaNewsOpportunityEvidenceBundle,
) -> tuple[Path, bool]:
    try:
        checked = AlpacaNewsOpportunityEvidenceBundle.model_validate(bundle.model_dump())
        path = root / f"alpaca_news_opportunity_evidence_{checked.bundle_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise AlpacaNewsOpportunityEvidenceError from None


def load_alpaca_news_opportunity_evidence(
    path: Path,
) -> AlpacaNewsOpportunityEvidenceBundle:
    try:
        payload = read_private_text(path)
        bundle = AlpacaNewsOpportunityEvidenceBundle.model_validate_json(payload)
        if (
            path.name != f"alpaca_news_opportunity_evidence_{bundle.bundle_id}.json"
            or payload != _payload(bundle)
        ):
            raise AlpacaNewsOpportunityEvidenceError
        return bundle
    except AlpacaNewsOpportunityEvidenceError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise AlpacaNewsOpportunityEvidenceError from None


def _payload(bundle: AlpacaNewsOpportunityEvidenceBundle) -> str:
    return canonical_experiment_ledger_json(bundle) + "\n"


__all__ = (
    "load_alpaca_news_opportunity_evidence",
    "publish_alpaca_news_opportunity_evidence",
)
