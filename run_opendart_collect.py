#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with rich --with typer python

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.kr_theme_models import KrCoverageStatus
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
    KrThemeWriterLeaseUnavailableError,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.opendart_client import (
    OpenDartClient,
    UnsafeOpenDartEndpointError,
    UnsafeOpenDartRedirectPolicyError,
)
from trading_agent.opendart_collection import (
    OpenDartCollectionResult,
    collect_opendart_disclosures,
)
from trading_agent.opendart_config import (
    DEFAULT_OPENDART_SECRET_PATH,
    InvalidOpenDartCredentialsError,
    OpenDartSecretEncodingError,
    OpenDartSecretFileError,
    create_opendart_http_client,
    load_opendart_credentials,
)
from trading_agent.opendart_fixture import (
    OpenDartFixtureError,
    load_opendart_fixture,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,122}$")


def main(
    collection_cycle_id: str | None = None,
    collection_date: str | None = None,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/opendart/latest",
    fixture_manifest: str | None = None,
    secret_path: str | None = None,
) -> None:
    if (
        collection_cycle_id is None
        or _SAFE_ID.fullmatch(collection_cycle_id) is None
    ):
        raise typer.BadParameter("유효한 collection cycle ID가 필요합니다")
    parsed_date = _collection_date(collection_date)
    if fixture_manifest is not None and secret_path is not None:
        raise typer.BadParameter("fixture mode에서는 secret path를 사용할 수 없습니다")
    try:
        store = KrThemeStore(Path(database))
        if fixture_manifest is not None:
            fetcher = load_opendart_fixture(
                Path(fixture_manifest),
                collection_date=parsed_date,
            )
            result = collect_opendart_disclosures(
                fetcher,
                store,
                collection_cycle_id=collection_cycle_id,
                collection_date=parsed_date,
            )
        else:
            credentials = load_opendart_credentials(
                DEFAULT_OPENDART_SECRET_PATH
                if secret_path is None
                else Path(secret_path)
            )
            with create_opendart_http_client() as http_client:
                result = collect_opendart_disclosures(
                    OpenDartClient(http_client, credentials),
                    store,
                    collection_cycle_id=collection_cycle_id,
                    collection_date=parsed_date,
                )
    except (
        InvalidKrThemeSourceError,
        InvalidOpenDartCredentialsError,
        KrThemeConflictError,
        KrThemeWriterLeaseUnavailableError,
        OpenDartFixtureError,
        OpenDartSecretEncodingError,
        OpenDartSecretFileError,
        UnsafeOpenDartEndpointError,
        UnsafeOpenDartRedirectPolicyError,
        UnsupportedKrThemeSchemaError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    except ValueError:
        raise typer.BadParameter(
            "OpenDART 입력 또는 source contract가 유효하지 않습니다"
        ) from None

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "opendart_collection_summary_ko.md"
    _ = report_path.write_text(
        _report(result, collection_date=parsed_date),
        encoding="utf-8",
    )
    report_path.chmod(0o600)
    if result.run.status is KrCoverageStatus.FAILED:
        raise typer.BadParameter(
            f"OpenDART source run 실패: {result.run.failure_code}"
        )
    rprint(
        f"[green]완료[/green] OpenDART receipt {result.receipt_count}건, "
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
        raise typer.BadParameter("collection date는 YYYY-MM-DD여야 합니다") from None
    if parsed.isoformat() != value:
        raise typer.BadParameter("collection date는 YYYY-MM-DD여야 합니다")
    return parsed


def _report(
    result: OpenDartCollectionResult,
    *,
    collection_date: dt.date,
) -> str:
    run = result.run
    return "\n".join(
        (
            "# OpenDART Read-Only Collection 요약",
            "",
            "> 공시 source 수집 감사이며 테마 추천이나 수익성 결과가 아닙니다.",
            "",
            f"- 수집 cycle: {run.collection_cycle_id}",
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
            "- 외부 mutation: 없음",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
