from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from tests.test_us_quote_actionability import (
    AT,
    SCAN_STARTED_AT,
    _conditional_publication,
    _quote,
)
from trading_agent.signal_contract_models import EvidenceRef
from trading_agent.us_quote_actionability import assess_us_quote
from trading_agent.us_quote_actionability_evidence import (
    UsQuotePolicyEvidence,
    evidence_from_kis_snapshot,
)
from trading_agent.us_quote_actionability_policy import assess_us_quote_evidence
from trading_agent.us_quote_actionability_projection import snapshot_from_kis


def test_kis_evidence_adapter_preserves_existing_policy_result() -> None:
    base = _conditional_publication()
    quote = _quote()
    snapshot = snapshot_from_kis(quote)
    evidence = evidence_from_kis_snapshot(snapshot)

    generic = assess_us_quote_evidence(
        base,
        evidence,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )
    existing = assess_us_quote(
        base,
        quote,
        scan_started_at=SCAN_STARTED_AT,
        evaluated_at=AT,
    )

    assert evidence.quote_id == snapshot.quote_id
    assert evidence.evidence_ref == EvidenceRef(
        namespace="quote/snapshot",
        record_id=snapshot.quote_id,
        observed_at=snapshot.provider_observed_at,
    )
    assert generic.assessment == existing.assessment
    assert generic.derived_publication == existing.derived_publication


def test_policy_evidence_rejects_source_time_mismatch() -> None:
    snapshot = snapshot_from_kis(_quote())
    evidence = evidence_from_kis_snapshot(snapshot)

    with pytest.raises(ValidationError):
        _ = UsQuotePolicyEvidence.model_validate(
            {
                **evidence.model_dump(),
                "evidence_ref": evidence.evidence_ref.model_copy(
                    update={"observed_at": evidence.provider_observed_at - dt.timedelta(microseconds=1)}
                ),
            }
        )


def test_policy_evidence_rejects_unbound_quote_identity() -> None:
    evidence = evidence_from_kis_snapshot(snapshot_from_kis(_quote()))

    with pytest.raises(ValidationError):
        _ = UsQuotePolicyEvidence.model_validate({**evidence.model_dump(), "quote_id": "provider-native-id"})
