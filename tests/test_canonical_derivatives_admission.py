from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading_agent.alpaca_option_chain_collection import collect_alpaca_option_chain
from trading_agent.alpaca_option_chain_models import (
    OptionChainRawResponse,
    OptionChainRequest,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore
from trading_agent.alpaca_option_contract_collection import collect_alpaca_option_contracts
from trading_agent.alpaca_option_contract_models import (
    OptionContractCatalogRequest,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore
from trading_agent.canonical_derivatives_models import (
    CanonicalDerivativesAdmissionRequest,
    CanonicalDerivativesReason,
    CanonicalDerivativesStatus,
)
from trading_agent.canonical_derivatives_projection import (
    project_canonical_derivatives_evidence,
)
from trading_agent.kis_futures_entitlement_admission import (
    KisFuturesAdmissionReason,
    KisFuturesAdmissionStatus,
    KisFuturesEntitlementAdmission,
    publish_kis_futures_entitlement_admission,
)

FIXTURES = Path(__file__).parent / "fixtures"
STARTED = dt.datetime(2026, 7, 23, 14, 30, tzinfo=dt.UTC)
COMPLETED = STARTED + dt.timedelta(minutes=2)
AS_OF = STARTED + dt.timedelta(minutes=10)
ROOT = Path(__file__).parents[1]


class _ContractFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        return OptionContractRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=STARTED + dt.timedelta(minutes=1),
            status_code=200,
            content_type="application/json",
            raw_payload=self.payload,
        )


class _ChainFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        return OptionChainRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=STARTED + dt.timedelta(minutes=1),
            status_code=200,
            content_type="application/json",
            raw_payload=self.payload,
        )


def test_actual_indicative_receipts_project_research_only_and_replay_without_mutation(
    tmp_path: Path,
) -> None:
    contract_store, chain_store, request = _stores(tmp_path)
    before = contract_store.counts(), chain_store.counts()

    first = project_canonical_derivatives_evidence(contract_store, chain_store, request)
    second = project_canonical_derivatives_evidence(contract_store, chain_store, request)

    assert first == second
    assert first.status is CanonicalDerivativesStatus.READY
    assert first.terminal_reason is CanonicalDerivativesReason.INDICATIVE_RESEARCH_ONLY
    assert first.source_id is not None
    assert first.source_id.provider == "alpaca"
    assert first.source_id.feed == "options_indicative"
    assert first.capability_state == "complete"
    assert first.entitlement_state == "research_only"
    assert first.current_authority is False
    assert first.selectable is False
    assert first.network_access == first.provider_mutation == first.account_or_order_mutation == 0
    assert first.contracts[0].provider_symbol == "AAPL260724C00200000"
    assert first.contracts[0].contract_observed_at == COMPLETED
    assert first.contracts[0].quote_observed_at == STARTED + dt.timedelta(minutes=1)
    assert (contract_store.counts(), chain_store.counts()) == before


@pytest.mark.parametrize(
    ("variant", "reason"),
    (
        ("missing", CanonicalDerivativesReason.OPTIONS_ENTITLEMENT_MISSING),
        ("stale", CanonicalDerivativesReason.DERIVATIVE_SURFACE_STALE),
        ("over_broad", CanonicalDerivativesReason.DERIVATIVES_EVIDENCE_OVER_BROAD),
        ("opra", CanonicalDerivativesReason.CURRENT_QUOTE_NOT_LICENSED),
        ("corrupt", CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID),
    ),
)
def test_admission_failures_are_exact_terminal_blockers(
    tmp_path: Path,
    variant: str,
    reason: CanonicalDerivativesReason,
) -> None:
    contract_store, chain_store, request = _stores(
        tmp_path,
        feed=OptionFeed.OPRA if variant == "opra" else OptionFeed.INDICATIVE,
        duplicate=variant == "over_broad",
    )
    if variant == "missing":
        chain_store = AlpacaOptionChainStore(tmp_path / "absent" / "chain.sqlite3")
    elif variant == "stale":
        request = request.model_copy(update={"as_of": AS_OF + dt.timedelta(days=1)})
    elif variant == "over_broad":
        request = request.model_copy(update={"max_contracts": 1})
    elif variant == "corrupt":
        chain_store.path.chmod(0o644)

    evidence = project_canonical_derivatives_evidence(contract_store, chain_store, request)

    assert evidence.status is CanonicalDerivativesStatus.BLOCKED
    assert evidence.terminal_reason is reason
    assert not evidence.contracts
    assert evidence.current_authority is False
    assert evidence.selectable is False


def test_reviewed_kis_cme_blocker_is_preserved(tmp_path: Path) -> None:
    contract_store, chain_store, request = _stores(tmp_path)
    admission = KisFuturesEntitlementAdmission(
        source_request_id="a" * 64,
        source_run_id="b" * 64,
        evidence_sha256="c" * 64,
        observed_at=AS_OF,
        status=KisFuturesAdmissionStatus.BLOCKED,
        reason=KisFuturesAdmissionReason.CME_SUB_ENTITLEMENT_MISSING,
        requested_contract_count=2,
        canonical_quote_count=0,
    )
    path, _ = publish_kis_futures_entitlement_admission(tmp_path / "kis", admission)
    request = request.model_copy(update={"kis_admission_path": path})

    evidence = project_canonical_derivatives_evidence(contract_store, chain_store, request)

    assert evidence.terminal_reason is CanonicalDerivativesReason.CME_SUB_ENTITLEMENT_MISSING
    assert evidence.status is CanonicalDerivativesStatus.BLOCKED


def test_cli_emits_stable_json_and_replay_keeps_source_counts(tmp_path: Path) -> None:
    contract_store, chain_store, _ = _stores(tmp_path)
    command = [
        sys.executable,
        str(ROOT / "run_canonical_derivatives_admission.py"),
        "--contract-collection-id",
        "canonical-contracts",
        "--chain-collection-id",
        "canonical-chain",
        "--underlying-symbol",
        "AAPL",
        "--expiration-date",
        "2026-07-24",
        "--contract-type",
        "call",
        "--contract-database",
        str(contract_store.path),
        "--chain-database",
        str(chain_store.path),
        "--as-of",
        AS_OF.isoformat(),
    ]
    before = contract_store.counts(), chain_store.counts()

    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)

    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert json.loads(first.stdout)["terminal_reason"] == "indicative_research_only"
    assert (contract_store.counts(), chain_store.counts()) == before


def _stores(
    tmp_path: Path,
    *,
    feed: OptionFeed = OptionFeed.INDICATIVE,
    duplicate: bool = False,
) -> tuple[
    AlpacaOptionContractStore,
    AlpacaOptionChainStore,
    CanonicalDerivativesAdmissionRequest,
]:
    contract_request = OptionContractCatalogRequest(
        collection_id="canonical-contracts",
        underlying_symbol="AAPL",
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )
    chain_request = OptionChainRequest(
        collection_id="canonical-chain",
        underlying_symbol="AAPL",
        feed=feed,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=1_000,
        max_pages=2,
    )
    contract_payload = _payload("alpaca_option_contract", duplicate)
    chain_payload = _payload("alpaca_option_chain", duplicate)
    contract_store = AlpacaOptionContractStore(tmp_path / "contracts" / "store.sqlite3")
    chain_store = AlpacaOptionChainStore(tmp_path / "chain" / "store.sqlite3")
    contract_store.preflight_write()
    chain_store.preflight_write()
    collect_alpaca_option_contracts(
        _ContractFetcher(contract_payload),
        contract_store,
        contract_request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )
    collect_alpaca_option_chain(
        _ChainFetcher(chain_payload),
        chain_store,
        chain_request,
        _clock=iter((STARTED, COMPLETED)).__next__,
    )
    return (
        contract_store,
        chain_store,
        CanonicalDerivativesAdmissionRequest(
            contract_request=contract_request,
            chain_request=chain_request,
            as_of=AS_OF,
        ),
    )


def _payload(name: str, duplicate: bool) -> bytes:
    path = FIXTURES / name / "page-001.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if duplicate and name == "alpaca_option_contract":
        second = dict(payload["option_contracts"][0])
        second.update(
            id="7e58f870-fe73-4583-81e4-b9a37892c36f",
            symbol="AAPL260724C00210000",
            strike_price="210",
        )
        payload["option_contracts"].append(second)
    elif duplicate:
        payload["snapshots"]["AAPL260724C00210000"] = payload["snapshots"]["AAPL260724C00200000"]
    return json.dumps(payload, separators=(",", ":")).encode()
