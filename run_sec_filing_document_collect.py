from __future__ import annotations

import datetime as dt
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
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.sec_edgar_config import (
    DEFAULT_SEC_USER_AGENT_PATH,
    create_sec_edgar_archive_http_client,
    load_sec_user_agent,
)
from trading_agent.sec_edgar_models import normalize_sec_cik
from trading_agent.sec_filing_document_client import SecFilingDocumentClient
from trading_agent.sec_filing_document_collection import collect_sec_filing_documents
from trading_agent.sec_filing_document_fixture import load_sec_filing_document_fixture
from trading_agent.sec_filing_document_models import (
    SecFilingDocumentRawResponse,
    SecFilingDocumentStatus,
    SecFilingDocumentTarget,
)
from trading_agent.sec_filing_document_store import SecFilingDocumentStore
from trading_agent.sec_filing_document_target import read_sec_filing_document_targets

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def main(
    parent_collection_id: str | None = None,
    cik: str | None = None,
    metadata_database: str = "outputs/us_regulatory/sec_edgar.sqlite3",
    document_database: str = "outputs/us_regulatory/sec_filing_documents.sqlite3",
    output_dir: str = "outputs/us_regulatory/sec/document-latest",
    max_documents: int = 1,
    fixture_manifest: str | None = None,
    user_agent_path: str | None = None,
) -> None:
    if parent_collection_id is None or _SAFE_ID.fullmatch(parent_collection_id) is None:
        raise typer.BadParameter("valid parent collection ID is required")
    try:
        normalized_cik = normalize_sec_cik(cik or "")
    except ValueError:
        raise typer.BadParameter("CIK must contain exactly 10 digits") from None
    if fixture_manifest is not None and user_agent_path is not None:
        raise typer.BadParameter("fixture mode cannot use a User-Agent file")
    report_path = Path(output_dir) / "sec_filing_document_summary.md"
    paths = (Path(metadata_database), Path(document_database), report_path)
    if any(_paths_alias(left, right) for index, left in enumerate(paths) for right in paths[index + 1 :]):
        raise typer.BadParameter("metadata, document, and report paths must be distinct")
    try:
        _preflight_report(report_path)
        targets = read_sec_filing_document_targets(
            Path(metadata_database),
            parent_collection_id,
            normalized_cik,
            limit=max_documents,
        )
        if not targets:
            raise ValueError
        store = SecFilingDocumentStore(Path(document_database))
        replayed = sum(
            1 for target in targets if store.path.exists() and store.run_for_target(target.target_id) is not None
        )
        if replayed == len(targets):
            fetcher = _ReplayOnlyFetcher()
            runs = collect_sec_filing_documents(fetcher, store, targets, _clock=_utc_now)
        elif fixture_manifest is not None:
            runs = collect_sec_filing_documents(
                load_sec_filing_document_fixture(Path(fixture_manifest)),
                store,
                targets,
                _clock=_utc_now,
            )
        else:
            setting = load_sec_user_agent(
                DEFAULT_SEC_USER_AGENT_PATH if user_agent_path is None else Path(user_agent_path)
            )
            with create_sec_edgar_archive_http_client() as http_client:
                runs = collect_sec_filing_documents(
                    SecFilingDocumentClient(http_client, setting),
                    store,
                    targets,
                    _clock=_utc_now,
                )
        report = _report(len(targets), replayed, runs)
        write_private_stable_report(report_path, report)
    except Exception:
        raise typer.BadParameter("SEC filing document collection state is invalid") from None
    failed = next(
        (run.failure_code for run in runs if run.status is SecFilingDocumentStatus.FAILED),
        None,
    )
    if failed is not None:
        raise typer.BadParameter(f"SEC filing document collection failed: {failed}")
    rprint(
        f"[green]complete[/green] SEC filing documents {len(runs)}, "
        f"bytes {sum(run.byte_count for run in runs)}, replayed {replayed}"
    )


class _ReplayOnlyFetcher:
    def fetch(self, target: SecFilingDocumentTarget) -> SecFilingDocumentRawResponse:
        _ = target
        raise RuntimeError


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _preflight_report(path: Path) -> None:
    target = absolute_private_path(path)
    if not target.name:
        raise ValueError
    parent = open_private_parent(target.parent, create=True)
    try:
        require_private_directory(parent)
        require_open_directory_path(target.parent, parent)
        try:
            descriptor = os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
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
        os.close(parent)


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        left = absolute_private_path(left)
        right = absolute_private_path(right)
        if left == right:
            return True
        return left.exists() and right.exists() and os.path.samestat(left.stat(), right.stat())
    except (OSError, RuntimeError, ValueError):
        raise typer.BadParameter("collection path is invalid") from None


def _report(selected: int, replayed: int, runs) -> str:
    return "\n".join(
        (
            "# SEC Filing Document Summary",
            "",
            "> Regulatory source evidence only; not a recommendation or profitability result.",
            "",
            f"- documents selected: {selected}",
            f"- documents completed: {len(runs)}",
            f"- documents replayed: {replayed}",
            f"- raw bytes retained: {sum(run.byte_count for run in runs)}",
            "- collection bound: maximum 8 documents per invocation",
            "- provider access: GET-only",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
