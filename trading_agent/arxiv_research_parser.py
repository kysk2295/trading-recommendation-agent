from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from trading_agent.arxiv_research_models import (
    ArxivPaper,
    ArxivRawReceipt,
    ArxivResearchError,
    ArxivResearchRequest,
    ArxivResearchSnapshot,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_OPEN = "{http://a9.com/-/spec/opensearch/1.1/}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def parse_arxiv_research(
    request: ArxivResearchRequest,
    receipt: ArxivRawReceipt,
) -> ArxivResearchSnapshot:
    try:
        if receipt.request_id != request.request_id or receipt.status_code != 200:
            raise ArxivResearchError
        lowered = receipt.raw_payload.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise ArxivResearchError
        root = ET.fromstring(receipt.raw_payload)
        if root.tag != f"{_ATOM}feed":
            raise ArxivResearchError
        total = _integer(root, f"{_OPEN}totalResults")
        start = _integer(root, f"{_OPEN}startIndex")
        items = _integer(root, f"{_OPEN}itemsPerPage")
        entries = tuple(root.findall(f"{_ATOM}entry"))
        if start != 0 or items != len(entries) or not 1 <= len(entries) <= request.max_results:
            raise ArxivResearchError
        papers = tuple(sorted((_paper(entry) for entry in entries), key=lambda paper: paper.arxiv_id))
        return ArxivResearchSnapshot(
            request_id=request.request_id,
            raw_receipt_id=receipt.receipt_id,
            observed_at=receipt.received_at,
            category=request.category,
            terms=request.terms,
            total_results=total,
            papers=papers,
        )
    except ArxivResearchError:
        raise
    except (ET.ParseError, TypeError, ValueError):
        raise ArxivResearchError from None


def _paper(entry: ET.Element) -> ArxivPaper:
    raw_id = _text(entry, f"{_ATOM}id")
    parsed = urlsplit(raw_id)
    if parsed.hostname != "arxiv.org" or not parsed.path.startswith("/abs/"):
        raise ArxivResearchError
    arxiv_id = parsed.path.removeprefix("/abs/")
    authors = tuple(
        _normalized(_text(author, f"{_ATOM}name"))
        for author in entry.findall(f"{_ATOM}author")
    )
    categories = tuple(
        sorted(
            {
                category.attrib.get("term", "")
                for category in entry.findall(f"{_ATOM}category")
            }
        )
    )
    doi = entry.findtext(f"{_ARXIV}doi")
    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=_normalized(_text(entry, f"{_ATOM}title")),
        summary=_normalized(_text(entry, f"{_ATOM}summary")),
        authors=authors,
        categories=categories,
        published_at=_time(_text(entry, f"{_ATOM}published")),
        updated_at=_time(_text(entry, f"{_ATOM}updated")),
        abstract_url=f"https://arxiv.org/abs/{arxiv_id}",
        doi=None if doi is None else _normalized(doi),
    )


def _integer(parent: ET.Element, name: str) -> int:
    return int(_text(parent, name))


def _text(parent: ET.Element, name: str) -> str:
    value = parent.findtext(name)
    if value is None:
        raise ArxivResearchError
    return value


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArxivResearchError
    return parsed.astimezone(dt.UTC)


__all__ = ("parse_arxiv_research",)
