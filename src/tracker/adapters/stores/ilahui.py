"""Ilahui (ilahuiperu.com) — Shopify storefront.

Primary path is the Shopify `search/suggest.json` predictive-search endpoint
(JSON, cheap, structured). The `search?q=...` HTML page is a mandatory
fallback (design.md: "the HTML fallback path is mandatory, not optional" —
`suggest.json` availability is a Shopify convention, not a guarantee).
`RawPayload.content_type` tells `parse()` which branch to use.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError, StoreParseError
from tracker.domain.matching import detect_language, detect_product_type
from tracker.domain.model import Availability, Money, Offer

STORE_SLUG = "ilahui"
BASE_URL = "https://ilahuiperu.com"

_PRICE_CLEAN_RE = re.compile(r"[^\d.,]")


def _parse_price_string(raw: str | None) -> Money | None:
    if not raw:
        return None
    cleaned = _PRICE_CLEAN_RE.sub("", raw).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return Money(amount=Decimal(cleaned), currency="PEN")
    except InvalidOperation as exc:
        raise StoreParseError(f"ilahui: invalid price value: {raw!r}") from exc


def _absolute_url(base_url: str, path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    return f"{base_url}{path_or_url}"


def parse_suggest_json(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: Shopify `suggest.json` body -> `Offer`s."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StoreParseError(f"ilahui: invalid suggest.json: {exc}") from exc

    try:
        products = data["resources"]["results"]["products"]
    except (KeyError, TypeError) as exc:
        raise StoreParseError(f"ilahui: unexpected suggest.json shape: {exc}") from exc

    offers: list[Offer] = []
    for product in products:
        try:
            external_id = str(product["id"])
            title = str(product["title"])
            url_path = str(product["url"])
            available = bool(product.get("available"))
        except (KeyError, TypeError) as exc:
            raise StoreParseError(f"ilahui: malformed suggest.json product: {exc}") from exc

        price = _parse_price_string(product.get("price"))
        availability = Availability.IN_STOCK if available else Availability.OUT_OF_STOCK
        offers.append(
            Offer(
                store=STORE_SLUG,
                external_id=external_id,
                title=title,
                url=_absolute_url(base_url, url_path),
                price=price,
                availability=availability,
                language=detect_language(title),
                product_type=detect_product_type(title),
                observed_at=observed_at,
            )
        )
    return offers


def parse_search_html(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: the `search?q=...` HTML page -> `Offer`s (fallback path).

    The theme renders each product card as `div[data-product-id]`, but the
    same id appears multiple times per page (desktop/mobile/quick-view
    variants); only the card carrying a title and price is a real result, and
    duplicates are skipped by id.
    """
    tree = HTMLParser(body)
    offers: list[Offer] = []
    seen_ids: set[str] = set()

    for card in tree.css("div[data-product-id]"):
        external_id = card.attributes.get("data-product-id")
        if not external_id or external_id in seen_ids:
            continue
        title_node = card.css_first(".m-product-card__name")
        price_node = card.css_first(".m-price-item--regular")
        if title_node is None or price_node is None:
            continue

        seen_ids.add(external_id)
        title = title_node.text(strip=True)
        href = title_node.attributes.get("href") or ""
        avail_node = card.css_first(".m-product-card__availability-tag")
        is_available = avail_node is not None and avail_node.attributes.get("data-available") == "true"

        offers.append(
            Offer(
                store=STORE_SLUG,
                external_id=external_id,
                title=title,
                url=_absolute_url(base_url, href),
                price=_parse_price_string(price_node.text(strip=True)),
                availability=Availability.IN_STOCK if is_available else Availability.OUT_OF_STOCK,
                language=detect_language(title),
                product_type=detect_product_type(title),
                observed_at=observed_at,
            )
        )
    return offers


class IlahuiAdapter:
    store_slug = STORE_SLUG

    def __init__(self, client: httpx.Client, base_url: str = BASE_URL) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def _fetch_suggest(self, query: str) -> RawPayload:
        url = (
            f"{self._base_url}/search/suggest.json"
            f"?q={quote(query)}&resources[type]=product&resources[limit]=10"
        )
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"ilahui: suggest.json request failed: {exc}") from exc
        # A 200 response is not proof of a real suggest.json endpoint: a themed
        # 404/redirect page, or a body that merely isn't JSON, must also trigger
        # the mandatory HTML fallback in fetch() below rather than being handed
        # to parse() as if it were valid — parse() only sees content already
        # confirmed to decode as JSON.
        try:
            json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise StoreFetchError(
                f"ilahui: suggest.json returned a non-JSON body (status {response.status_code}): {exc}"
            ) from exc
        return RawPayload(
            store=self.store_slug,
            source_url=url,
            content_type="application/json",
            body=response.text,
            fetched_at=datetime.now(UTC),
        )

    def _fetch_html(self, query: str) -> RawPayload:
        url = f"{self._base_url}/search?q={quote(query)}&type=product"
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"ilahui: HTML search request failed: {exc}") from exc
        return RawPayload(
            store=self.store_slug,
            source_url=url,
            content_type="text/html",
            body=response.text,
            fetched_at=datetime.now(UTC),
        )

    def fetch(self, query: str) -> Sequence[RawPayload]:
        try:
            return [self._fetch_suggest(query)]
        except StoreFetchError as primary_exc:
            try:
                return [self._fetch_html(query)]
            except StoreFetchError as fallback_exc:
                raise StoreFetchError(
                    f"ilahui: both suggest.json and HTML fallback failed "
                    f"(json: {primary_exc}; html: {fallback_exc})"
                ) from fallback_exc

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        offers: list[Offer] = []
        for payload in payloads:
            if "json" in payload.content_type:
                offers.extend(parse_suggest_json(payload.body, observed_at, self._base_url))
            else:
                offers.extend(parse_search_html(payload.body, observed_at, self._base_url))
        return offers

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        return self.parse(self.fetch(query), observed_at)
