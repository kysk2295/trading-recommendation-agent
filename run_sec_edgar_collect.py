#!/usr/bin/env -S uv run --python 3.12 --with httpx2 --with pydantic --with rich --with typer python

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.sec_edgar_client import (
    SecEdgarClient,
    UnsafeSecEdgarEndpointError,
    UnsafeSecEdgarRedirectPolicyError,
)
from trading_agent.sec_edgar_collection import (
    SecCollectionResult,
    collect_sec_submissions,
    resume_sec_collection,
)
from trading_agent.sec_edgar_config import (
    DEFAULT_SEC_USER_AGENT_PATH,
    InvalidSecUserAgentError,
    SecUserAgentFileError,
    create_sec_edgar_http_client,
    load_sec_user_agent,
)
from trading_agent.sec_edgar_fixture import SecEdgarFixtureError, load_sec_edgar_fixture
from trading_agent.sec_edgar_models import SecCollectionStatus, normalize_sec_cik
from trading_agent.sec_edgar_store import InvalidSecEdgarStoreError, SecEdgarStore

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def main(
    collection_id: str | None = None,
    cik: str | None = None,
    database: str = "outputs/us_regulatory/sec_edgar.sqlite3",
    output_dir: str = "outputs/us_regulatory/sec/latest",
    fixture_manifest: str | None = None,
    user_agent_path: str | None = None,
) -> None:
    if collection_id is None or _SAFE_ID.fullmatch(collection_id) is None:
        raise typer.BadParameter("valid collection ID is required")
    try:
        normalized_cik = normalize_sec_cik(cik or "")
    except ValueError:
        raise typer.BadParameter("CIK must contain exactly 10 digits") from None
    if fixture_manifest is not None and user_agent_path is not None:
        raise typer.BadParameter("fixture mode cannot use a User-Agent file")
    try:
        store = SecEdgarStore(Path(database))
        result = resume_sec_collection(store, collection_id, normalized_cik)
        if result is None and fixture_manifest is not None:
            result = collect_sec_submissions(
                load_sec_edgar_fixture(Path(fixture_manifest)),
                store,
                collection_id,
                normalized_cik,
            )
        elif result is None:
            setting = load_sec_user_agent(
                DEFAULT_SEC_USER_AGENT_PATH if user_agent_path is None else Path(user_agent_path)
            )
            with create_sec_edgar_http_client() as http_client:
                result = collect_sec_submissions(
                    SecEdgarClient(http_client, setting),
                    store,
                    collection_id,
                    normalized_cik,
                )
    except (
        InvalidSecEdgarStoreError,
        InvalidSecUserAgentError,
        SecEdgarFixtureError,
        SecUserAgentFileError,
        UnsafeSecEdgarEndpointError,
        UnsafeSecEdgarRedirectPolicyError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    except ValueError:
        raise typer.BadParameter("SEC EDGAR input or source contract is invalid") from None
    _write_report(Path(output_dir), _report(result))
    if result.run.status is SecCollectionStatus.FAILED:
        raise typer.BadParameter(f"SEC EDGAR collection failed: {result.run.failure_code}")
    rprint(
        f"[green]complete[/green] SEC EDGAR filings {result.filing_count}, "
        f"new versions {result.new_filing_version_count}, replayed {result.replayed}"
    )


def _report(result: SecCollectionResult) -> str:
    return "\n".join(
        (
            "# SEC EDGAR Read-Only Collection Summary",
            "",
            "> Regulatory source evidence only; not a recommendation or profitability result.",
            "",
            f"- collection status: {result.run.status.value}",
            f"- failure code: {result.run.failure_code or 'none'}",
            f"- receipt created: {'yes' if result.receipt_created else 'no'}",
            f"- recent filings: {result.filing_count}",
            f"- new filing versions: {result.new_filing_version_count}",
            f"- additional history files discovered: {result.run.additional_history_file_count}",
            f"- replayed: {'yes' if result.replayed else 'no'}",
            "- additional history fetched: no",
            "- broker or external mutation: none",
            "",
        )
    )


def _write_report(directory: Path, content: str) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / "sec_edgar_collection_summary.md"
    if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
        raise typer.BadParameter("SEC EDGAR report path is invalid")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        _ = handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise typer.BadParameter("SEC EDGAR report path is invalid")


if __name__ == "__main__":
    typer.run(main)
