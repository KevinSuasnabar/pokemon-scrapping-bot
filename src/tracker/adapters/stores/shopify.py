"""Shared Shopify storefront logic — Ilahui and Pharmax are both Shopify,
same reasoning as vtex.py being shared by Plaza Vea and Oechsle.

Primary path is the Shopify `search/suggest.json` predictive-search endpoint
(JSON, cheap, structured, identical shape across every Shopify store — this
is Shopify's own API, not a per-store theme detail). The `search?q=...` HTML
page is a mandatory fallback (design.md: "the HTML fallback path is
mandatory, not optional" — `suggest.json` availability is a Shopify
convention, not a guarantee) for when the suggest.json *request itself*
fails — but unlike the JSON API, the HTML markup is rendered by each store's
own theme, so `parse_search_html` is NOT shared: each store subclasses
`ShopifyStoreAdapter` and provides its own theme-specific HTML parser.
"""

from __future__ import annotations

import json
import re
import sys
from abc import abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError, StoreParseError
from tracker.domain.matching import detect_language, detect_product_type
from tracker.domain.model import Availability, Money, Offer
from tracker.infrastructure.http.client import get_with_retry

#: Matches the actual number, ignoring whatever currency prefix precedes it.
#: Stripping non-digit characters (the earlier approach) breaks on "S/. 99.90"
#: (Pharmax's format — note the period after "S/") because the stray prefix
#: period survives the strip and collides with the decimal point, producing
#: invalid Decimal syntax; anchoring on "starts with a digit" sidesteps that
#: regardless of what punctuation the currency prefix uses.
_PRICE_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


def _parse_price_string(store_slug: str, raw: str | None) -> Money | None:
    if not raw:
        return None
    match = _PRICE_NUMBER_RE.search(raw)
    if match is None:
        return None
    cleaned = match.group(0).replace(",", "")
    try:
        return Money(amount=Decimal(cleaned), currency="PEN")
    except InvalidOperation as exc:
        raise StoreParseError(f"{store_slug}: invalid price value: {raw!r}") from exc


def _absolute_url(base_url: str, path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    return f"{base_url}{path_or_url}"


def parse_suggest_json(store_slug: str, body: str, observed_at: datetime, base_url: str) -> list[Offer]:
    """Pure: Shopify `suggest.json` body -> `Offer`s. Identical shape on
    every Shopify store — this is Shopify's own predictive-search API."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StoreParseError(f"{store_slug}: invalid suggest.json: {exc}") from exc

    try:
        products = data["resources"]["results"]["products"]
    except (KeyError, TypeError) as exc:
        raise StoreParseError(f"{store_slug}: unexpected suggest.json shape: {exc}") from exc

    offers: list[Offer] = []
    for product in products:
        try:
            external_id = str(product["id"])
            title = str(product["title"])
            url_path = str(product["url"])
            available = bool(product.get("available"))
        except (KeyError, TypeError) as exc:
            # One malformed entry must not lose every other valid product on
            # the same page (same reasoning as vtex.py's parse_products).
            print(f"{store_slug}: skipping malformed suggest.json product: {exc}", file=sys.stderr)
            continue

        price = _parse_price_string(store_slug, product.get("price"))
        availability = Availability.IN_STOCK if available else Availability.OUT_OF_STOCK
        offers.append(
            Offer(
                store=store_slug,
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


class ShopifyStoreAdapter:
    """Base adapter for any Shopify-backed storefront. Subclasses provide
    `store_slug`, `base_url`, and their own theme-specific `parse_search_html`
    (see module docstring — only the JSON path is truly shared)."""

    def __init__(self, store_slug: str, base_url: str, client: httpx.Client) -> None:
        self.store_slug = store_slug
        self._base_url = base_url.rstrip("/")
        self._client = client

    @abstractmethod
    def parse_search_html(self, body: str, observed_at: datetime) -> list[Offer]:
        """Pure: this store's theme-specific `search?q=...` HTML page ->
        `Offer`s (fallback path, used only when suggest.json's request
        itself fails)."""
        raise NotImplementedError

    def _fetch_suggest(self, query: str) -> RawPayload:
        url = (
            f"{self._base_url}/search/suggest.json"
            f"?q={quote(query)}&resources[type]=product&resources[limit]=10"
        )
        try:
            response = get_with_retry(self._client, url)
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"{self.store_slug}: suggest.json request failed: {exc}") from exc
        # A 200 response is not proof of a real suggest.json endpoint: a themed
        # 404/redirect page, or a body that merely isn't JSON, must also trigger
        # the mandatory HTML fallback in fetch() below rather than being handed
        # to parse() as if it were valid — parse() only sees content already
        # confirmed to decode as JSON.
        try:
            json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise StoreFetchError(
                f"{self.store_slug}: suggest.json returned a non-JSON body "
                f"(status {response.status_code}): {exc}"
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
            response = get_with_retry(self._client, url)
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"{self.store_slug}: HTML search request failed: {exc}") from exc
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
                    f"{self.store_slug}: both suggest.json and HTML fallback failed "
                    f"(json: {primary_exc}; html: {fallback_exc})"
                ) from fallback_exc

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        offers: list[Offer] = []
        for payload in payloads:
            if "json" in payload.content_type:
                offers.extend(parse_suggest_json(self.store_slug, payload.body, observed_at, self._base_url))
            else:
                offers.extend(self.parse_search_html(payload.body, observed_at))
        return offers

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        return self.parse(self.fetch(query), observed_at)
