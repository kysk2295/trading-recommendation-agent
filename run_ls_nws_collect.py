#!/usr/bin/env -S uv run --python 3.12 --with httpx2 --with pydantic --with rich --with typer --with websockets python

from __future__ import annotations

import datetime as dt
import math
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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
from trading_agent.ls_config import (
    DEFAULT_LS_SECRET_PATH,
    InvalidLsCredentialsError,
    LsSecretEncodingError,
    LsSecretFileError,
    create_ls_http_client,
    load_ls_credentials,
)
from trading_agent.ls_nws_collection import (
    MAX_LS_NWS_COLLECTION_FRAMES,
    MAX_LS_NWS_COLLECTION_SECONDS,
    LsNwsCollectionInputError,
    LsNwsCollectionResult,
    LsNwsFrameReceiver,
    collect_ls_nws_news,
)
from trading_agent.ls_nws_fixture import (
    LsNwsFixtureError,
    load_ls_nws_fixture,
)
from trading_agent.ls_nws_stream import (
    UnsafeLsNwsStreamEndpointError,
    open_ls_nws_stream,
)
from trading_agent.ls_token import (
    UnsafeLsTokenEndpointError,
    UnsafeLsTokenRedirectPolicyError,
    issue_ls_access_token,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,121}$")


def main(
    collection_cycle_id: str | None = None,
    collection_date: str | None = None,
    duration_seconds: float = 60.0,
    max_frames: int = 1_000,
    database: str = "outputs/kr_theme/kr_theme.sqlite3",
    output_dir: str = "outputs/kr_theme/ls_nws/latest",
    fixture_manifest: str | None = None,
    secret_path: str | None = None,
) -> None:
    if (
        collection_cycle_id is None
        or _SAFE_ID.fullmatch(collection_cycle_id) is None
    ):
        raise typer.BadParameter("유효한 collection cycle ID가 필요합니다")
    parsed_date = _collection_date(collection_date)
    if (
        not math.isfinite(duration_seconds)
        or not 0 < duration_seconds <= MAX_LS_NWS_COLLECTION_SECONDS
    ):
        raise typer.BadParameter("duration seconds 범위가 유효하지 않습니다")
    if (
        isinstance(max_frames, bool)
        or not 1 <= max_frames <= MAX_LS_NWS_COLLECTION_FRAMES
    ):
        raise typer.BadParameter("max frames 범위가 유효하지 않습니다")
    if fixture_manifest is not None and secret_path is not None:
        raise typer.BadParameter("fixture mode에서는 secret path를 사용할 수 없습니다")
    database_path = Path(database)
    report_path = Path(output_dir) / "ls_nws_collection_summary_ko.md"
    report_target = report_path.expanduser().resolve(strict=False)
    ledger_targets = {
        candidate.expanduser().resolve(strict=False)
        for candidate in (
            database_path,
            Path(f"{database_path}.writer.lock"),
            Path(f"{database_path}-journal"),
            Path(f"{database_path}-shm"),
            Path(f"{database_path}-wal"),
        )
    }
    if report_target in ledger_targets:
        raise typer.BadParameter("database와 report 경로는 겹칠 수 없습니다")

    try:
        store = KrThemeStore(database_path)
        if fixture_manifest is not None:
            selected_fixture_manifest = Path(fixture_manifest)

            @contextmanager
            def open_fixture_source() -> Iterator[LsNwsFrameReceiver]:
                fixture_source = load_ls_nws_fixture(selected_fixture_manifest)
                with fixture_source.open() as receiver:
                    yield receiver

            opener = open_fixture_source
        else:
            selected_secret_path = (
                DEFAULT_LS_SECRET_PATH
                if secret_path is None
                else Path(secret_path)
            )

            @contextmanager
            def open_live_source() -> Iterator[LsNwsFrameReceiver]:
                credentials = load_ls_credentials(selected_secret_path)
                with create_ls_http_client() as http_client:
                    access_token = issue_ls_access_token(
                        http_client,
                        credentials,
                    )
                with open_ls_nws_stream(access_token) as stream:
                    yield stream

            opener = open_live_source
        result = collect_ls_nws_news(
            opener,
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=parsed_date,
            duration_seconds=duration_seconds,
            max_frames=max_frames,
        )
    except (
        InvalidKrThemeSourceError,
        InvalidLsCredentialsError,
        KrThemeConflictError,
        KrThemeWriterLeaseUnavailableError,
        LsNwsCollectionInputError,
        LsNwsFixtureError,
        LsSecretEncodingError,
        LsSecretFileError,
        UnsafeLsNwsStreamEndpointError,
        UnsafeLsTokenEndpointError,
        UnsafeLsTokenRedirectPolicyError,
        UnsupportedKrThemeSchemaError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    except ValueError:
        raise typer.BadParameter(
            "LS NWS 입력 또는 source contract가 유효하지 않습니다"
        ) from None

    _write_private_text(
        report_path,
        _report(result, collection_date=parsed_date),
    )
    if result.run.status is KrCoverageStatus.FAILED:
        raise typer.BadParameter(
            f"LS NWS source run 실패: {result.run.failure_code}"
        )
    rprint(
        f"[green]완료[/green] LS NWS receipt {result.receipt_count}건, "
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


def _write_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise OSError("unsafe temporary report file")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _report(
    result: LsNwsCollectionResult,
    *,
    collection_date: dt.date,
) -> str:
    run = result.run
    return "\n".join(
        (
            "# LS NWS Read-Only Collection 요약",
            "",
            "> 뉴스 source 수집 감사이며 종목 추천이나 수익성 결과가 아닙니다.",
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
