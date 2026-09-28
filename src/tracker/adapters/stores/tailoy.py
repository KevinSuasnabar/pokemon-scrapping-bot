"""Tai Loy (tailoy.com.pe) — Magento storefront.

No JSON API exists (confirmed live, 2026-09-28): the search results page
(`/catalogsearch/result/?q=...`) is server-rendered HTML, so this is a
straight HTML-scrape adapter (like Ilahui's/Pharmax's fallback path, except
here there is no JSON primary path to fall back FROM).

Availability signal (confirmed live, user-verified by inspecting real
button markup, not by reading visible text alone): Magento renders a
genuinely purchasable product with `<button type="submit" ... title="Agregar">`
that submits the actual add-to-cart form. A "30th Anniversary" listing that
is really a pre-order renders `<button type="button" ... title="Ver Preventa">`
instead — same visual position, but `type="button"` only redirects to a
separate pre-order page; it never adds anything to the cart. No numeric
stock quantity is exposed anywhere on the public storefront (Magento
platform convention, confirmed by inspecting the product detail page).

The combined check (`type="submit"` AND the title doesn't mention
preventa/agotado) is intentionally redundant across two independent HTML
signals — structural (`type`) and textual (`title`) — rather than trusting
either alone, per explicit user request.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser, Node

from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError, StoreParseError
from tracker.domain.matching import detect_language, detect_product_type
from tracker.domain.model import Availability, Money, Offer
from tracker.infrastructure.http.client import get_with_retry

STORE_SLUG = "tailoy"
BASE_URL = "https://www.tailoy.com.pe"

_PRICE_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")
_PRODUCT_ID_RE = re.compile(r"-(\d+)\.html$")
_NOT_REALLY_AVAILABLE_MARKERS = ("preventa", "agotado", "notify")


def _parse_price(raw: str | None) -> Money | None:
    if not raw:
        return None
    match = _PRICE_NUMBER_RE.search(raw)
    if match is None:
        return None
    cleaned = match.group(0).replace(",", "")
    try:
        return Money(amount=Decimal(cleaned), currency="PEN")
    except InvalidOperation as exc:
        raise StoreParseError(f"{STORE_SLUG}: invalid price value: {raw!r}") from exc


def _external_id_from_url(url: str) -> str | None:
    match = _PRODUCT_ID_RE.search(url)
    return match.group(1) if match else None


def _is_really_available(card: Node) -> bool:
    """`type="submit"` (a real add-to-cart form submit, not a redirect) AND
    the button's title doesn't mention pre-order/sold-out — see module
    docstring. No button at all (or `type != "submit"`) means not available;
    treated as False rather than raising, since "no purchasable state found"
    is itself a valid (negative) answer, not a parse error."""
    button = card.css_first('button[type="submit"]')
    if button is None:
        return False
    title = (button.attributes.get("title") or "").lower()
    return not any(marker in title for marker in _NOT_REALLY_AVAILABLE_MARKERS)


def parse_search_html(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: the `/catalogsearch/result/?q=...` HTML page -> `Offer`s."""
    tree = HTMLParser(body)
    offers: list[Offer] = []
    seen_ids: set[str] = set()

    for card in tree.css("li.product-item"):
        title_node = card.css_first(".product-item-link")
        price_node = card.css_first(".price")
        if title_node is None or price_node is None:
            continue

        href = title_node.attributes.get("href") or ""
        external_id = _external_id_from_url(href)
        if not external_id or external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        title = title_node.text(strip=True)
        availability = Availability.IN_STOCK if _is_really_available(card) else Availability.OUT_OF_STOCK

        offers.append(
            Offer(
                store=STORE_SLUG,
                external_id=external_id,
                title=title,
                url=href,
                price=_parse_price(price_node.text(strip=True)),
                availability=availability,
                language=detect_language(title),
                product_type=detect_product_type(title),
                observed_at=observed_at,
            )
        )
    return offers


class TaiLoyAdapter:
    store_slug = STORE_SLUG

    def __init__(self, client: httpx.Client, base_url: str = BASE_URL) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def _search_url(self, query: str) -> str:
        return f"{self._base_url}/catalogsearch/result/?q={quote(query)}"

    def fetch(self, query: str) -> Sequence[RawPayload]:
        url = self._search_url(query)
        try:
            response = get_with_retry(self._client, url)
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"{self.store_slug}: request to {url} failed: {exc}") from exc
        return [
            RawPayload(
                store=self.store_slug,
                source_url=url,
                content_type=response.headers.get("content-type", "text/html"),
                body=response.text,
                fetched_at=datetime.now(UTC),
            )
        ]

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        offers: list[Offer] = []
        for payload in payloads:
            offers.extend(parse_search_html(payload.body, observed_at, self._base_url))
        return offers

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        return self.parse(self.fetch(query), observed_at)
