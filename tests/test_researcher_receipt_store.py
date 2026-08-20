from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from trading_agent.researcher_receipt_store import ResearcherReceiptStore, ResearcherReceiptStoreError


def test_require_call_verifies_immutable_record_prompt_and_response(tmp_path) -> None:
    store = ResearcherReceiptStore(tmp_path / "receipts")
    receipt = store.record_call(
        model_id="day-agent-coder-v1",
        prompt="bounded prompt",
        response=b'{"kind":"hypothesis_submission"}',
        seed=7,
        temperature=0.0,
        called_at=dt.datetime(2026, 8, 21, 14, 30, 10, tzinfo=dt.UTC),
    )

    verified = store.require_call(receipt)

    assert verified.record.model_id == receipt.model_id
    assert verified.prompt == "bounded prompt"
    assert verified.response == b'{"kind":"hypothesis_submission"}'


def test_require_call_rejects_forged_receipt_metadata(tmp_path) -> None:
    store = ResearcherReceiptStore(tmp_path / "receipts")
    receipt = store.record_call(
        model_id="day-agent-coder-v1",
        prompt="bounded prompt",
        response=b"bounded response",
        seed=7,
        temperature=0.0,
        called_at=dt.datetime(2026, 8, 21, 14, 30, 10, tzinfo=dt.UTC),
    )

    with pytest.raises(ResearcherReceiptStoreError):
        store.require_call(replace(receipt, prompt_sha256="f" * 64))
