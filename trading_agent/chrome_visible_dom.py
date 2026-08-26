from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.browser_observation_redaction import redact_browser_observation_text
from trading_agent.chrome_devtools_types import InvalidChromeDevToolsError
from trading_agent.local_browser_protocol import (
    BrowserPageObservation,
    BrowserVisibleLink,
    InvalidLocalBrowserProtocolError,
    require_public_https_url,
)

VISIBLE_DOM_EXPRESSION: Final = r"""(() => JSON.stringify((() => {
const blocked = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
const intersects = (rect) => rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0
  && rect.left < innerWidth && rect.top < innerHeight;
const rendered = (element) => {
  for (let current = element; current; current = current.parentElement) {
    if (blocked.has(current.tagName) || current.hidden || current.getAttribute('aria-hidden') === 'true') return false;
    const style = getComputedStyle(current);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse'
        || Number(style.opacity) === 0) return false;
  }
  return true;
};
const visibleText = (root, limit) => {
  if (!root) return '';
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const parts = [];
  let length = 0;
  for (let node = walker.nextNode(); node && length < limit; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent || !rendered(parent)) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const visible = Array.from(range.getClientRects()).some(intersects);
    range.detach();
    if (!visible) continue;
    const value = String(node.nodeValue || '').replace(/\s+/g, ' ').trim();
    if (!value) continue;
    const prefix = parts.length ? ' ' : '';
    const fragment = (prefix + value).slice(0, limit - length);
    parts.push(fragment); length += fragment.length;
  }
  return parts.join('');
};
const links = [];
for (const anchor of document.querySelectorAll('a[href]')) {
  if (links.length === 40 || !rendered(anchor)
      || !Array.from(anchor.getClientRects()).some(intersects)) continue;
  const url = String(anchor.href || '');
  if (!url.startsWith('https://') || url.length > 2048) continue;
  links.push({label: visibleText(anchor, 200), url});
}
return {title: String(document.title || '').slice(0, 500),
  url: String(location.href || '').slice(0, 2048),
  text: visibleText(document.body, 12000), links};
})()))()"""


class _BoundaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True, hide_input_in_errors=True)


class _PageLink(_BoundaryModel):
    label: str = Field(max_length=500)
    url: str = Field(max_length=4_096)


class _PagePayload(_BoundaryModel):
    title: str = Field(max_length=1_000)
    url: str = Field(max_length=4_096)
    text: str = Field(max_length=1024 * 1024)
    links: tuple[_PageLink, ...] = Field(max_length=100)


def parse_visible_page(target_id: str, value: str, captured_at: datetime) -> BrowserPageObservation:
    try:
        payload = _PagePayload.model_validate_json(value)
        url = require_public_https_url(payload.url)
        links: list[BrowserVisibleLink] = []
        for candidate in payload.links:
            if not 8 <= len(candidate.url) <= 2_048:
                continue
            try:
                normalized = require_public_https_url(candidate.url)
            except InvalidLocalBrowserProtocolError:
                continue
            label = redact_browser_observation_text(candidate.label.strip())[:200]
            links.append(BrowserVisibleLink(label=label, url=normalized))
            if len(links) == 40:
                break
        return BrowserPageObservation(
            target_id=target_id,
            url=url,
            title=redact_browser_observation_text(payload.title)[:500],
            visible_text=redact_browser_observation_text(payload.text)[:12_000],
            links=tuple(links),
            captured_at=captured_at,
        )
    except (InvalidLocalBrowserProtocolError, ValidationError):
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None


__all__ = ["VISIBLE_DOM_EXPRESSION", "parse_visible_page"]
