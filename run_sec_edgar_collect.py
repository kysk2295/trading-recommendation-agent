#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic", "rich", "typer"]
# ///
# How to run:
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run --script run_sec_edgar_collect.py --help
# 3. Or: chmod +x run_sec_edgar_collect.py && ./run_sec_edgar_collect.py --help

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import typer
from rich import print as rprint

from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.sec_edgar_client import (
    SecEdgarClient,
    UnsafeSecEdgarEndpointError,
    UnsafeSecEdgarRedirectPolicyError,
)
from trading_agent.sec_edgar_collection import (
    InvalidSecEdgarCollectionError,
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
from trading_agent.sec_edgar_models import SecCollectionStatus, SecEdgarResponseError, normalize_sec_cik
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
    report_path = Path(output_dir) / "sec_edgar_collection_summary.md"
    if _paths_alias(Path(database), report_path):
        raise typer.BadParameter("database and report paths must be distinct")
    try:
        _preflight_report(report_path)
    except (OSError, ValueError):
        raise typer.BadParameter("SEC EDGAR report path is invalid") from None
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
        InvalidSecEdgarCollectionError,
        InvalidSecUserAgentError,
        SecEdgarResponseError,
        SecEdgarFixtureError,
        SecUserAgentFileError,
        UnsafeSecEdgarEndpointError,
        UnsafeSecEdgarRedirectPolicyError,
    ) as error:
        raise typer.BadParameter(str(error)) from None
    try:
        write_private_stable_report(report_path, _report(result))
    except InvalidPrivateStableReportError:
        raise typer.BadParameter("SEC EDGAR report path is invalid") from None
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


def _preflight_report(path: Path) -> None:
    target = absolute_private_path(path)
    if not target.name:
        raise ValueError
    parent_descriptor = open_private_parent(target.parent, create=True)
    try:
        require_private_directory(parent_descriptor)
        require_open_directory_path(target.parent, parent_descriptor)
        try:
            descriptor = os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            return
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise ValueError
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def _paths_alias(left: Path, right: Path) -> bool:
    left = absolute_private_path(left)
    right = absolute_private_path(right)
    if left == right:
        return True
    try:
        return left.exists() and right.exists() and os.path.samestat(left.stat(), right.stat())
    except OSError:
        raise typer.BadParameter("database or report path is invalid") from None


if __name__ == "__main__":
    typer.run(main)
