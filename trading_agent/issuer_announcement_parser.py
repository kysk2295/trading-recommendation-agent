from __future__ import annotations

import datetime as dt
import hashlib
import xml.etree.ElementTree as element_tree
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from trading_agent.issuer_announcement_models import (
    IssuerAnnouncementContractError,
    IssuerAnnouncementEvent,
    IssuerAnnouncementFeedFormat,
    IssuerAnnouncementOnboarding,
    IssuerAnnouncementRawReceipt,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_XML_CONTENT_TYPES = frozenset(
    {
        "application/atom+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    }
)


def parse_issuer_announcement_feed(
    onboarding: IssuerAnnouncementOnboarding,
    receipt: IssuerAnnouncementRawReceipt,
) -> tuple[IssuerAnnouncementEvent, ...]:
    if receipt.status_code != 200 or receipt.content_type not in _XML_CONTENT_TYPES:
        raise IssuerAnnouncementContractError
    raw = receipt.raw_payload
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise IssuerAnnouncementContractError
    try:
        root = element_tree.fromstring(raw)
    except element_tree.ParseError:
        raise IssuerAnnouncementContractError from None
    match onboarding.feed_format:
        case IssuerAnnouncementFeedFormat.RSS2:
            values = _rss_events(root, onboarding, receipt)
        case IssuerAnnouncementFeedFormat.ATOM:
            values = _atom_events(root, onboarding, receipt)
    if len(values) > onboarding.max_items:
        raise IssuerAnnouncementContractError
    identities = tuple(value.provider_event_id for value in values)
    if len(identities) != len(set(identities)):
        raise IssuerAnnouncementContractError
    return tuple(sorted(values, key=lambda item: (item.published_at, item.provider_event_id)))


def _rss_events(
    root: element_tree.Element,
    onboarding: IssuerAnnouncementOnboarding,
    receipt: IssuerAnnouncementRawReceipt,
) -> list[IssuerAnnouncementEvent]:
    if root.tag != "rss":
        raise IssuerAnnouncementContractError
    channel = root.find("channel")
    if channel is None:
        raise IssuerAnnouncementContractError
    return [
        _event(
            onboarding,
            receipt,
            provider_event_id=_text(item, "guid"),
            title=_text(item, "title"),
            url=_text(item, "link"),
            published_at=_rss_time(_text(item, "pubDate")),
        )
        for item in channel.findall("item")
    ]


def _atom_events(
    root: element_tree.Element,
    onboarding: IssuerAnnouncementOnboarding,
    receipt: IssuerAnnouncementRawReceipt,
) -> list[IssuerAnnouncementEvent]:
    if root.tag != f"{_ATOM}feed":
        raise IssuerAnnouncementContractError
    result: list[IssuerAnnouncementEvent] = []
    for entry in root.findall(f"{_ATOM}entry"):
        links = tuple(
            item.attrib.get("href", "")
            for item in entry.findall(f"{_ATOM}link")
            if item.attrib.get("rel", "alternate") == "alternate"
        )
        published = entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")
        if len(links) != 1 or published is None:
            raise IssuerAnnouncementContractError
        result.append(
            _event(
                onboarding,
                receipt,
                provider_event_id=_text(entry, f"{_ATOM}id"),
                title=_text(entry, f"{_ATOM}title"),
                url=links[0],
                published_at=_iso_time(published),
            )
        )
    return result


def _event(
    onboarding: IssuerAnnouncementOnboarding,
    receipt: IssuerAnnouncementRawReceipt,
    *,
    provider_event_id: str,
    title: str,
    url: str,
    published_at: dt.datetime,
) -> IssuerAnnouncementEvent:
    parsed = urlsplit(url)
    if (
        not title
        or title != title.strip()
        or len(title) > 1_000
        or any(character < " " for character in title)
        or parsed.hostname not in onboarding.allowed_hosts
        or published_at > receipt.received_at
    ):
        raise IssuerAnnouncementContractError
    return IssuerAnnouncementEvent(
        source_id=onboarding.source_id,
        issuer_id=onboarding.issuer_id,
        provider_event_id=provider_event_id,
        symbols=onboarding.symbols,
        published_at=published_at,
        url=url,
        title_sha256=hashlib.sha256(title.encode()).hexdigest(),
        raw_receipt_id=receipt.receipt_id,
    )


def _text(parent: element_tree.Element, name: str) -> str:
    value = parent.findtext(name)
    if value is None or value != value.strip():
        raise IssuerAnnouncementContractError
    return value


def _rss_time(value: str) -> dt.datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        raise IssuerAnnouncementContractError from None
    return _utc(parsed)


def _iso_time(value: str) -> dt.datetime:
    try:
        return _utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        raise IssuerAnnouncementContractError from None


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IssuerAnnouncementContractError
    return value.astimezone(dt.UTC)


__all__ = ("parse_issuer_announcement_feed",)
