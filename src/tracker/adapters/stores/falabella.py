"""Saga Falabella (falabella.com.pe).

Same discovery mechanism as Ripley: a Next.js page embedding its product
list as JSON in a `<script id="__NEXT_DATA__">` tag. Two lessons learned on
Ripley were confirmed to apply here too, live, 2026-09-28:

1. **Marketplace filter.** Falabella's own direct sales carry
   `sellerId == "FALABELLA_PERU"` in search results; every other seller ID
   (confirmed live: several distinct third-party sellers, e.g. "Raymi Store",
   "Pokeperu Store", "Supermegapop") is a marketplace reseller, routinely
   priced well above Falabella's own price for comparable items — same
   inflated-price risk as Ripley's marketplace, same fix: only
   `FALABELLA_PERU` listings are tracked.

2. **Real-stock confirmation.** Search results' `availability` field is NOT
   a stock signal — it's an object of shipping-method eligibility strings,
   frequently all empty, unrelated to whether the item is actually in
   stock. Worse: on the one product checked in depth, the product DETAIL
   page's own `isOutOfStock: False` directly CONTRADICTED its own
   `stockUnits: {sku: 0}` on the same product — the boolean was simply
   wrong. `stockUnits` (a real per-SKU unit count) is Falabella's ground
   truth; `isOutOfStock` is not trusted at all. Same confirmation pattern as
   Ripley's `xcatentryQuantity`: only offers search reports as apparently
   available get a follow-up per-product detail-page fetch to confirm a
   positive `stockUnits` count before being reported as IN_STOCK.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError, StoreParseError
from tracker.domain.matching import detect_language, detect_product_type
from tracker.domain.model import Availability, Money, Offer
from tracker.infrastructure.http.client import get_with_retry

STORE_SLUG = "falabella"
BASE_URL = "https://www.falabella.com.pe"
_SITE_SLUG = "falabella-pe"  # Falabella's own multi-tenant catalog slug

#: Falabella's own direct-sale identity in search results — confirmed live.
_DIRECT_SELLER_ID = "FALABELLA_PERU"

#: Preference order when a product carries several price types at once
#: (varies by product/seller — confirmed live to be either
#: eventPrice/cmrPrice/normalPrice OR internetPrice/normalPrice).
_PREFERRED_PRICE_TYPES = ("eventPrice", "internetPrice", "cmrPrice")

_PRICE_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


def _parse_money(raw: str) -> Money | None:
    match = _PRICE_NUMBER_RE.search(raw)
    if match is None:
        return None
    cleaned = match.group(0).replace(",", "")
    try:
        return Money(amount=Decimal(cleaned), currency="PEN")
    except InvalidOperation as exc:
        raise StoreParseError(f"{STORE_SLUG}: invalid price value: {raw!r}") from exc


def _extract_price(prices: list[dict[str, Any]]) -> Money | None:
    """Prefer a non-crossed price of a preferred type; fall back to any
    non-crossed price, then to the first price entry regardless."""
    by_type = {p.get("type"): p for p in prices if isinstance(p, dict)}
    for price_type in _PREFERRED_PRICE_TYPES:
        entry = by_type.get(price_type)
        if entry and not entry.get("crossed"):
            values = entry.get("price") or []
            if values:
                return _parse_money(str(values[0]))
    for entry in prices:
        if isinstance(entry, dict) and not entry.get("crossed"):
            values = entry.get("price") or []
            if values:
                return _parse_money(str(values[0]))
    if prices and isinstance(prices[0], dict) and prices[0].get("price"):
        return _parse_money(str(prices[0]["price"][0]))
    return None


def parse_search_page(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: the `/falabella-pe/search?Ntt=...` page -> `Offer`s, via its
    embedded `__NEXT_DATA__` JSON."""
    tree = HTMLParser(body)
    script_node = tree.css_first("script#__NEXT_DATA__")
    if script_node is None:
        raise StoreParseError(f"{STORE_SLUG}: __NEXT_DATA__ script not found in search page")

    try:
        data = json.loads(script_node.text())
        results = data["props"]["pageProps"]["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StoreParseError(f"{STORE_SLUG}: unexpected __NEXT_DATA__ shape: {exc}") from exc

    offers: list[Offer] = []
    for product in results:
        try:
            sku_id = str(product["skuId"])
            title = str(product["displayName"])
            url = str(product["url"])
        except (KeyError, TypeError) as exc:
            # One malformed entry must not lose every other valid product on
            # the same page (same reasoning as vtex.py's parse_products).
            print(f"{STORE_SLUG}: skipping malformed product entry: {exc}", file=sys.stderr)
            continue

        # Marketplace filter — see module docstring point 1.
        if product.get("sellerId") != _DIRECT_SELLER_ID:
            continue

        offers.append(
            Offer(
                store=STORE_SLUG,
                external_id=sku_id,
                title=title,
                url=url,
                price=_extract_price(product.get("prices") or []),
                # Deliberately not derived from `availability` (see module
                # docstring point 2) — every offer starts OUT_OF_STOCK here
                # and only the detail-page confirmation step (search()
                # below) can promote it to IN_STOCK, so there is no
                # intermediate "apparently available" boolean to trust at
                # the parse stage at all.
                availability=Availability.OUT_OF_STOCK,
                language=detect_language(title),
                product_type=detect_product_type(title),
                observed_at=observed_at,
            )
        )
    return offers


def parse_product_detail_stock(body: str, sku_id: str) -> int | None:
    """Pure: a product detail page's embedded `__NEXT_DATA__` ->
    `stockUnits[sku_id]` (the real unit count — see module docstring point 2
    for why `isOutOfStock` is never trusted). `sku_id` is supplied by the
    caller (it already knows which product it asked for — this function
    doesn't try to re-derive it from the page). Returns `None` (not an
    exception) on anything unparseable, including a missing entry for this
    exact `sku_id` — this is a best-effort confirmation step, the caller
    decides how to treat "could not confirm"."""
    tree = HTMLParser(body)
    script_node = tree.css_first("script#__NEXT_DATA__")
    if script_node is None:
        return None
    try:
        data = json.loads(script_node.text())
        stock_units = data["props"]["pageProps"]["productData"]["stockUnits"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(stock_units, dict) or sku_id not in stock_units:
        return None
    quantity = stock_units[sku_id]
    return int(quantity) if isinstance(quantity, int | float) else None


class FalabellaAdapter:
    store_slug = STORE_SLUG

    def __init__(self, client: httpx.Client, base_url: str = BASE_URL) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def _search_url(self, query: str) -> str:
        return f"{self._base_url}/{_SITE_SLUG}/search?Ntt={quote(query)}"

    def fetch(self, query: str) -> Sequence[RawPayload]:
        url = self._search_url(query)
        try:
            response = get_with_retry(self._client, url)
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"{STORE_SLUG}: request to {url} failed: {exc}") from exc
        return [
            RawPayload(
                store=STORE_SLUG,
                source_url=url,
                content_type=response.headers.get("content-type", "text/html"),
                body=response.text,
                fetched_at=datetime.now(UTC),
            )
        ]

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        offers: list[Offer] = []
        for payload in payloads:
            offers.extend(parse_search_page(payload.body, observed_at, self._base_url))
        return offers

    def _confirm_real_stock(self, offer: Offer) -> Offer:
        """Every offer from `parse()` starts OUT_OF_STOCK (see
        parse_search_page's docstring) — this is what promotes a candidate
        to IN_STOCK, never the other direction, so a fetch failure or
        unparseable page simply leaves the offer as-is (fails closed)."""
        try:
            response = get_with_retry(self._client, offer.url)
        except httpx.HTTPError:
            return offer
        quantity = parse_product_detail_stock(response.text, offer.external_id)
        if quantity is not None and quantity > 0:
            return replace(offer, availability=Availability.IN_STOCK)
        return offer

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        offers = self.parse(self.fetch(query), observed_at)
        return [self._confirm_real_stock(offer) for offer in offers]
