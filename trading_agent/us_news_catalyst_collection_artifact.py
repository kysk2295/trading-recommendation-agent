from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_news_catalyst_collection_models import (
    InvalidUsNewsCatalystCollectionModelError,
    UsNewsCatalystCollectionPlan,
    UsNewsCatalystCollectionReceipt,
)

_PLAN_PREFIX = "us_news_catalyst_collection_plan"
_RECEIPT_PREFIX = "us_news_catalyst_collection_receipt"


def collection_plan_path(root: Path, cohort_artifact_id: str) -> Path:
    return root / f"{_PLAN_PREFIX}_{cohort_artifact_id}.json"


def collection_receipt_path(root: Path, cohort_artifact_id: str) -> Path:
    return root / f"{_RECEIPT_PREFIX}_{cohort_artifact_id}.json"


def publish_us_news_catalyst_collection_plan(
    root: Path,
    plan: UsNewsCatalystCollectionPlan,
) -> tuple[Path, bool]:
    path = collection_plan_path(root, plan.content.cohort_artifact_id)
    return _publish(path, plan)


def publish_us_news_catalyst_collection_receipt(
    root: Path,
    receipt: UsNewsCatalystCollectionReceipt,
) -> tuple[Path, bool]:
    path = collection_receipt_path(root, receipt.content.cohort_artifact_id)
    return _publish(path, receipt)


def load_us_news_catalyst_collection_plan(path: Path) -> UsNewsCatalystCollectionPlan:
    try:
        payload = read_private_text(path)
        plan = UsNewsCatalystCollectionPlan.model_validate_json(payload)
        if path.name != collection_plan_path(path.parent, plan.content.cohort_artifact_id).name:
            raise InvalidUsNewsCatalystCollectionModelError
        if payload != canonical_experiment_ledger_json(plan) + "\n":
            raise InvalidUsNewsCatalystCollectionModelError
        return plan
    except InvalidUsNewsCatalystCollectionModelError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystCollectionModelError from None


def load_us_news_catalyst_collection_receipt(
    path: Path,
) -> UsNewsCatalystCollectionReceipt:
    try:
        payload = read_private_text(path)
        receipt = UsNewsCatalystCollectionReceipt.model_validate_json(payload)
        if path.name != collection_receipt_path(path.parent, receipt.content.cohort_artifact_id).name:
            raise InvalidUsNewsCatalystCollectionModelError
        if payload != canonical_experiment_ledger_json(receipt) + "\n":
            raise InvalidUsNewsCatalystCollectionModelError
        return receipt
    except InvalidUsNewsCatalystCollectionModelError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystCollectionModelError from None


def _publish(
    path: Path,
    artifact: UsNewsCatalystCollectionPlan | UsNewsCatalystCollectionReceipt,
) -> tuple[Path, bool]:
    try:
        content = canonical_experiment_ledger_json(artifact) + "\n"
        return path, publish_private_immutable_text(path, content)
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystCollectionModelError from None


__all__ = (
    "collection_plan_path",
    "collection_receipt_path",
    "load_us_news_catalyst_collection_plan",
    "load_us_news_catalyst_collection_receipt",
    "publish_us_news_catalyst_collection_plan",
    "publish_us_news_catalyst_collection_receipt",
)
