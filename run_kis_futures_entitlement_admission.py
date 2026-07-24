#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.kis_futures_entitlement_admission import (
    KisFuturesAdmissionError,
    KisFuturesAdmissionStatus,
    KisFuturesEntitlementAdmission,
    evaluate_kis_futures_entitlement_admission,
    publish_kis_futures_entitlement_admission,
)
from trading_agent.kis_overseas_futures_models import KisFuturesQuoteRequest
from trading_agent.kis_overseas_futures_store import (
    KisOverseasFuturesStore,
    KisOverseasFuturesStoreError,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME = "kis_futures_entitlement_admission_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="저장된 KIS futures quote run의 entitlement를 query-only 판정"
    )
    parser.add_argument("--root-symbol", required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = KisFuturesQuoteRequest(
            root_symbol=args.root_symbol,
            symbols=tuple(
                value.strip().upper()
                for value in args.symbols.split(",")
                if value.strip()
            ),
        )
        admission = evaluate_kis_futures_entitlement_admission(
            KisOverseasFuturesStore(args.database),
            request,
        )
        _, created = publish_kis_futures_entitlement_admission(
            args.output_dir,
            admission,
        )
        write_private_stable_report(
            args.output_dir / REPORT_NAME,
            _report(admission, created),
        )
    except (
        InvalidPrivateStableReportError,
        KisFuturesAdmissionError,
        KisOverseasFuturesStoreError,
        OSError,
        TypeError,
        ValueError,
    ):
        return 1
    return {
        KisFuturesAdmissionStatus.READY: 0,
        KisFuturesAdmissionStatus.BLOCKED: 2,
        KisFuturesAdmissionStatus.UNKNOWN: 1,
    }[admission.status]


def _report(
    admission: KisFuturesEntitlementAdmission,
    created: bool,
) -> str:
    return "\n".join(
        (
            "# KIS Futures Entitlement Admission",
            "",
            "> Query-only source admission; not a recommendation or order.",
            "",
            f"- status: {admission.status.value}",
            f"- reason: {admission.reason.value}",
            f"- requested contracts: {admission.requested_contract_count}",
            f"- canonical quotes: {admission.canonical_quote_count}",
            f"- artifact created: {'yes' if created else 'no'}",
            "- network access: 0",
            "- credential read: 0",
            "- broker, account, position, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
