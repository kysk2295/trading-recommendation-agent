#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import typer
from rich import print as rprint

from scr_backtest.kis_intraday import KisApiError, MissingKisCredentialsError
from trading_agent.kis_auth import (
    KisMode,
    UnsafeSecretFileError,
    create_kis_client,
    get_access_token,
    load_kis_credentials,
)
from trading_agent.kis_kr_ranking import (
    KisKrRankingClient,
    KisKrRankingTransportError,
    UnsafeKisKrRankingEndpointError,
    UnsafeKisKrRankingRedirectPolicyError,
)
from trading_agent.kis_kr_ranking_collection import (
    KisKrRankingCollectionResult,
    collect_kis_kr_rankings,
    resume_kis_kr_ranking_collection,
)
from trading_agent.kis_kr_ranking_fixture import (
    KisKrRankingFixtureError,
    load_kis_kr_ranking_fixture,
)
from trading_agent.kr_theme_models import KrCoverageStatus
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.private_report import write_private_report

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,115}$")
_KST = ZoneInfo("Asia/Seoul")


class _ProductionDateMismatchError(ValueError):
    pass


def main(
    collection_cycle_id: str | None = None,
    collection_date: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/kis_kr_ranking/latest",
    fixture_manifest: str | None = None,
) -> None:
    if collection_cycle_id is None or _SAFE_ID.fullmatch(collection_cycle_id) is None:
        raise typer.BadParameter("유효한 collection cycle ID가 필요합니다")
    parsed_date = _collection_date(collection_date)

    try:
        store = KrThemeStore(Path(database))
        result = resume_kis_kr_ranking_collection(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=parsed_date,
        )
        if result is not None:
            pass
        elif fixture_manifest is not None:
            fetcher = load_kis_kr_ranking_fixture(
                Path(fixture_manifest),
                collection_date=parsed_date,
            )
            result = collect_kis_kr_rankings(
                fetcher,
                store,
                collection_cycle_id=collection_cycle_id,
                collection_date=parsed_date,
            )
        else:
            if parsed_date != _current_kst_date():
                raise _ProductionDateMismatchError
            credentials = load_kis_credentials(KisMode.LIVE)
            with create_kis_client(KisMode.LIVE) as http_client:
                token = get_access_token(
                    http_client,
                    credentials,
                    KisMode.LIVE,
                )
                result = collect_kis_kr_rankings(
                    KisKrRankingClient(http_client, credentials, token),
                    store,
                    collection_cycle_id=collection_cycle_id,
                    collection_date=parsed_date,
                )
    except _ProductionDateMismatchError:
        raise typer.BadParameter(
            "production collection date는 현재 KST 날짜여야 합니다"
        ) from None
    except (
        InvalidKrThemeSourceError,
        KisKrRankingFixtureError,
        KrThemeConflictError,
        KrThemeWriterLeaseUnavailableError,
        UnsafeKisKrRankingEndpointError,
        UnsafeKisKrRankingRedirectPolicyError,
        UnsupportedKrThemeSchemaError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    except (
        FileNotFoundError,
        httpx2.HTTPError,
        KisApiError,
        KisKrRankingTransportError,
        MissingKisCredentialsError,
        PermissionError,
        UnsafeSecretFileError,
    ):
        raise typer.BadParameter(
            "KIS read-only credential 또는 transport preflight에 실패했습니다"
        ) from None
    except ValueError:
        raise typer.BadParameter(
            "KIS KR ranking 입력 또는 source contract가 유효하지 않습니다"
        ) from None

    report_path = Path(output_dir) / "kis_kr_ranking_collection_summary_ko.md"
    write_private_report(
        report_path,
        _report(result, collection_date=parsed_date),
    )
    if result.run.status is KrCoverageStatus.FAILED:
        raise typer.BadParameter(
            f"KIS KR ranking source run 실패: {result.run.failure_code}"
        )
    rprint(
        "[green]완료[/green] KIS KR ranking "
        + f"receipt {result.receipt_count}건, "
        + f"catalyst {result.catalyst_count}건, "
        + f"신규 receipt {result.new_receipt_count}건, "
        + f"신규 catalyst {result.new_catalyst_count}건"
    )


def _collection_date(value: str | None) -> dt.date:
    if value is None:
        raise typer.BadParameter("collection date가 필요합니다")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(
            "collection date는 YYYY-MM-DD여야 합니다"
        ) from None
    if parsed.isoformat() != value:
        raise typer.BadParameter("collection date는 YYYY-MM-DD여야 합니다")
    return parsed


def _current_kst_date() -> dt.date:
    return dt.datetime.now(_KST).date()


def _report(
    result: KisKrRankingCollectionResult,
    *,
    collection_date: dt.date,
) -> str:
    run = result.run
    return "\n".join(
        (
            "# KIS KR Ranking Read-Only Collection 요약",
            "",
            "> 국내 랭킹 source 수집 감사이며 추천이나 수익성 결과가 아닙니다.",
            "",
            f"- 수집 날짜: {collection_date.isoformat()}",
            f"- source 상태: {run.status.value}",
            f"- failure code: {run.failure_code or '없음'}",
            f"- receipt: {result.receipt_count}",
            f"- 신규 receipt: {result.new_receipt_count}",
            f"- catalyst: {result.catalyst_count}",
            f"- 신규 catalyst: {result.new_catalyst_count}",
            f"- 신규 observation: {result.new_observation_count}",
            f"- 재시작 no-op: {'예' if result.restarted else '아니오'}",
            "- 네 source 최종 cycle 확정: 아니오",
            "- 현재 호가·TradeSignal·외부 mutation: 없음",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
