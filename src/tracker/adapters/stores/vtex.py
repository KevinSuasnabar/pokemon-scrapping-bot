"""VTEX storefront adapter base — Plaza Vea and Oechsle both run on VTEX.

`fetch()` (I/O) and `parse_products()` (pure) are split per design decision #2:
`parse_products()` is fixture-tested with zero network and zero mocking.
"""

from __future__ import annotations

import json
import re
import sys
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

#: VTEX paginates in blocks of 50; stop after this many pages even if the
#: catalog claims more (design.md "Pagination (VTEX)").
MAX_PAGES = 5
PAGE_SIZE = 50

_TOTAL_COUNT_RE = re.compile(r"/\s*(\d+)\s*$")
#: Real VTEX deployments have been observed using any of these header names
#: for the "start-end/total" pagination range (design assumed `content-range`
#: / `resources-count-range`; the Task 4.1 fixture capture also observed the
#: plain `resources` header on live Plaza Vea/Oechsle responses).
_PAGINATION_HEADER_NAMES = ("content-range", "resources-count-range", "resources")


def _parse_total_count(headers: httpx.Headers) -> int | None:
    for header_name in _PAGINATION_HEADER_NAMES:
        value = headers.get(header_name)
        if not value:
            continue
        match = _TOTAL_COUNT_RE.search(value)
        if match:
            return int(match.group(1))
    return None


def _extract_price(commertial_offer: dict[str, object]) -> Money | None:
    price = commertial_offer.get("Price")
    if price is None:
        return None
    try:
        amount = Decimal(str(price))
    except InvalidOperation as exc:
        raise StoreParseError(f"invalid VTEX price value: {price!r}") from exc
    if amount <= 0:
        # A direct seller with no stock reports `Price: 0` (confirmed live,
        # 2026-09-28, alongside the marketplace-seller fix above) — that's
        # "not offered", not a real S/ 0.00 price.
        return None
    return Money(amount=amount, currency="PEN")


def _extract_availability(commertial_offer: dict[str, object]) -> Availability:
    available_quantity = commertial_offer.get("AvailableQuantity")
    if isinstance(available_quantity, int | float) and available_quantity > 0:
        return Availability.IN_STOCK
    if commertial_offer.get("IsAvailable"):
        return Availability.IN_STOCK
    return Availability.OUT_OF_STOCK


#: VTEX reserves sellerId "1" for the storefront's own first-party seller;
#: any other sellerId is a marketplace reseller (same platform convention
#: Falabella's `_DIRECT_SELLER_ID` filter and Ripley's `seller == "MARKETPLACE"`
#: check target on their own APIs). Confirmed live, 2026-09-28: 22/50 sampled
#: Pokemon products on Oechsle (1/50 on Plaza Vea) list a marketplace seller
#: FIRST — with `sellerDefault: true` and real price/quantity — and the
#: store's own listing second, at `Price: 0, AvailableQuantity: 0`. Taking
#: `sellers[0]` unconditionally (the pre-fix behavior) silently reported the
#: marketplace's price and stock as if they were the retailer's own.
_DIRECT_SELLER_ID = "1"


def _select_direct_seller(sellers: list[dict[str, object]]) -> dict[str, object] | None:
    for seller in sellers:
        if seller.get("sellerId") == _DIRECT_SELLER_ID:
            return seller
    return None


def _extract_attributes(product: dict[str, object]) -> dict[str, list[str]]:
    """VTEX specification groups appear as top-level `"Key": ["Value", ...]`
    entries on the product dict. Pull every string-list field; `detect_language`
    filters to the keys it actually cares about (`LANGUAGE_SPEC_KEYS`)."""
    attributes: dict[str, list[str]] = {}
    for key, value in product.items():
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            attributes[key] = value
    return attributes


def _parse_one_product(
    store_slug: str, product: dict[str, object], observed_at: datetime, base_url: str
) -> Offer | None:
    """`None` means "this product has no direct-retailer offer" (marketplace-
    only listing) — the caller skips it, same as a malformed entry, but it's
    not an error: the store simply doesn't sell it itself."""
    try:
        external_id = str(product["productId"])
        title = str(product["productName"])
        items = product["items"]
        item = items[0]  # type: ignore[index]
        sellers = item["sellers"]
    except (KeyError, IndexError, TypeError) as exc:
        raise StoreParseError(f"{store_slug}: malformed VTEX product entry: {exc}") from exc

    seller = _select_direct_seller(sellers)
    if seller is None:
        return None

    try:
        commertial_offer = seller["commertialOffer"]
    except (KeyError, TypeError) as exc:
        raise StoreParseError(f"{store_slug}: malformed VTEX product entry: {exc}") from exc
    if not isinstance(commertial_offer, dict):
        raise StoreParseError(f"{store_slug}: malformed VTEX product entry: commertialOffer is not an object")

    link = product.get("link")
    if isinstance(link, str) and link:
        url = link
    else:
        link_text = product.get("linkText", external_id)
        url = f"{base_url}/{link_text}/p"

    price = _extract_price(commertial_offer)
    availability = _extract_availability(commertial_offer)
    attributes = _extract_attributes(product)
    language = detect_language(title, attributes or None)
    product_type = detect_product_type(title)

    return Offer(
        store=store_slug,
        external_id=external_id,
        title=title,
        url=url,
        price=price,
        availability=availability,
        language=language,
        product_type=product_type,
        observed_at=observed_at,
    )


def parse_products(
    store_slug: str, body: str, observed_at: datetime, base_url: str = ""
) -> list[Offer]:
    """Pure (aside from a stderr warning on a skipped entry — same tradeoff
    `_run_loop`/`TelegramReporter` already make elsewhere in this codebase):
    a JSON array of VTEX product dicts -> `Offer`s. No network.

    A single malformed product is skipped, not a page-level failure: real
    Oechsle data was observed (12 occurrences within a 20-minute window,
    2026-09-18) missing `items[0].sellers` on one product while the rest of
    the page was fine — losing every other valid product to one bad entry
    is worse than just skipping it.
    """
    try:
        products = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StoreParseError(f"{store_slug}: invalid VTEX JSON: {exc}") from exc

    if not isinstance(products, list):
        raise StoreParseError(f"{store_slug}: expected a JSON array of products")

    offers: list[Offer] = []
    for product in products:
        try:
            offer = _parse_one_product(store_slug, product, observed_at, base_url)
        except StoreParseError as exc:
            print(f"{store_slug}: skipping malformed product: {exc}", file=sys.stderr)
            continue
        if offer is not None:
            offers.append(offer)
    return offers


class VtexStoreAdapter:
    """Base adapter for any VTEX-backed storefront. Subclasses (or config
    instances) only need `store_slug` and `base_url`."""

    def __init__(self, store_slug: str, base_url: str, client: httpx.Client) -> None:
        self.store_slug = store_slug
        self._base_url = base_url.rstrip("/")
        self._client = client

    def _search_url(self, query: str, offset: int) -> str:
        to_index = offset + PAGE_SIZE - 1
        return (
            f"{self._base_url}/api/catalog_system/pub/products/search"
            f"?ft={quote(query)}&_from={offset}&_to={to_index}"
        )

    def fetch(self, query: str) -> Sequence[RawPayload]:
        payloads: list[RawPayload] = []
        offset = 0
        for _page in range(MAX_PAGES):
            url = self._search_url(query, offset)
            try:
                response = get_with_retry(self._client, url)
            except httpx.HTTPError as exc:
                raise StoreFetchError(f"{self.store_slug}: request to {url} failed: {exc}") from exc

            payloads.append(
                RawPayload(
                    store=self.store_slug,
                    source_url=url,
                    content_type=response.headers.get("content-type", "application/json"),
                    body=response.text,
                    fetched_at=datetime.now(UTC),
                )
            )

            total = _parse_total_count(response.headers)
            if total is None:
                break  # missing header -> single page, no crash (design.md)
            offset += PAGE_SIZE
            if offset >= total:
                break

        return payloads

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        offers: list[Offer] = []
        for payload in payloads:
            offers.extend(parse_products(self.store_slug, payload.body, observed_at, self._base_url))
        return offers

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        return self.parse(self.fetch(query), observed_at)

    def fetch_known(self, identifiers: Sequence[str], observed_at: datetime) -> list[Offer]:
        """`SupportsKnownProducts`: `fq=productId:<id>` is the same catalog
        search API `fetch()` uses, just filtered to one exact product —
        confirmed live 2026-09-29 against a real Plaza Vea product ID.
        Reuses `parse_products` unchanged; a product this store simply
        doesn't carry (bad/stale id) returns an empty array, not an error.
        A network failure on one identifier is skipped, not fatal to the
        rest — same "one bad known-SKU shouldn't kill the others" philosophy
        as Ripley's/Tai Loy's `fetch_known`, deliberately not propagated
        like `fetch()`'s own (search) failures are."""
        offers: list[Offer] = []
        for product_id in identifiers:
            url = f"{self._base_url}/api/catalog_system/pub/products/search?fq=productId:{quote(product_id)}"
            try:
                response = get_with_retry(self._client, url)
            except httpx.HTTPError as exc:
                print(f"{self.store_slug}: skipping known product {product_id}: {exc}", file=sys.stderr)
                continue
            offers.extend(parse_products(self.store_slug, response.text, observed_at, self._base_url))
        return offers
