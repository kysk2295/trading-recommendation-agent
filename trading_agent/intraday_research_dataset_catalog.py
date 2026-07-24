from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from trading_agent.challenger_replay_models import ReplaySourceRejectedError
from trading_agent.challenger_replay_source import load_replay_source
from trading_agent.intraday_research_dataset import materialize_intraday_research_dataset
from trading_agent.intraday_research_dataset_catalog_models import (
    MAX_INTRADAY_RESEARCH_CANDIDATE_SESSIONS,
    IntradayResearchDatasetCatalogError,
    IntradayResearchDatasetCatalogReceipt,
    IntradayResearchDatasetCatalogRequest,
    IntradayResearchDatasetCatalogResult,
    IntradayResearchDatasetSessionAudit,
)
from trading_agent.intraday_research_dataset_models import (
    IntradayResearchDatasetError,
    IntradayResearchDatasetRequest,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)


def materialize_intraday_research_dataset_catalog(
    request: IntradayResearchDatasetCatalogRequest,
) -> IntradayResearchDatasetCatalogResult:
    _validate_request(request)
    audits: list[IntradayResearchDatasetSessionAudit] = []
    eligible = []
    for session_dir in sorted(request.session_dirs, key=lambda item: item.name):
        try:
            source = load_replay_source(session_dir)
        except ReplaySourceRejectedError as error:
            audits.append(
                IntradayResearchDatasetSessionAudit(
                    session_name=session_dir.name,
                    session_date=error.session_date,
                    eligible=False,
                    reason_codes=error.reasons,
                )
            )
        else:
            audits.append(
                IntradayResearchDatasetSessionAudit(
                    session_name=session_dir.name,
                    session_date=source.session_date,
                    eligible=True,
                    reason_codes=(),
                )
            )
            eligible.append((source.session_date, session_dir))
    dates = tuple(item[0] for item in eligible)
    if len(set(dates)) != len(dates):
        raise IntradayResearchDatasetCatalogError("duplicate_eligible_session_date")
    selected = tuple(path for _, path in sorted(eligible)[-request.max_sessions :])
    selected_dates = tuple(sorted(dates)[-request.max_sessions :])
    if len(selected) < request.minimum_sessions:
        raise IntradayResearchDatasetCatalogError("minimum_clean_sessions_not_met")
    if not set(request.required_session_dates).issubset(selected_dates):
        raise IntradayResearchDatasetCatalogError("required_clean_session_not_selected")
    try:
        dataset = materialize_intraday_research_dataset(
            IntradayResearchDatasetRequest(
                session_dirs=selected,
                output_root=request.output_root,
                max_sessions=request.max_sessions,
                max_bars=request.max_bars,
                producer_commit_sha=request.producer_commit_sha,
            )
        )
        receipt = IntradayResearchDatasetCatalogReceipt(
            dataset_input_sha256=dataset.input_sha256,
            dataset_receipt_name=dataset.receipt_path.name,
            minimum_sessions=request.minimum_sessions,
            candidate_sessions=len(request.session_dirs),
            required_session_dates=request.required_session_dates,
            selected_session_dates=selected_dates,
            selected_source_sha256s=dataset.source_session_sha256s,
            audits=tuple(audits),
        )
        payload = json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"
        digest = hashlib.sha256(payload.encode()).hexdigest()
        receipt_path = request.output_root / f"intraday_research_catalog_{digest}.json"
        receipt_created = publish_private_immutable_text(receipt_path, payload)
    except IntradayResearchDatasetCatalogError:
        raise
    except (
        IntradayResearchDatasetError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayResearchDatasetCatalogError("dataset_or_publication_invalid") from None
    return IntradayResearchDatasetCatalogResult(
        dataset=dataset,
        catalog_receipt_path=receipt_path,
        catalog_receipt_sha256=digest,
        candidate_sessions=len(request.session_dirs),
        blocked_sessions=sum(not item.eligible for item in audits),
        created=dataset.created or receipt_created,
    )


def _validate_request(request: IntradayResearchDatasetCatalogRequest) -> None:
    names = tuple(path.name for path in request.session_dirs)
    if (
        not names
        or len(names) > MAX_INTRADAY_RESEARCH_CANDIDATE_SESSIONS
        or len(set(names)) != len(names)
        or request.minimum_sessions < 1
        or request.minimum_sessions > request.max_sessions
        or request.max_sessions < 1
        or request.max_sessions > 60
        or request.max_bars < 1
        or request.max_bars > 100_000
        or len(set(request.required_session_dates)) != len(request.required_session_dates)
    ):
        raise IntradayResearchDatasetCatalogError("invalid_catalog_budget_or_identity")


__all__ = (
    "IntradayResearchDatasetCatalogError",
    "IntradayResearchDatasetCatalogRequest",
    "IntradayResearchDatasetCatalogResult",
    "materialize_intraday_research_dataset_catalog",
)
