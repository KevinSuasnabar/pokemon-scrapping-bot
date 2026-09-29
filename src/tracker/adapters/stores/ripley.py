"""Ripley (simple.ripley.com.pe).

**Task 0 spike outcome (recorded `2026-09-17`, see `scripts/spike_ripley.py`
and `tests/fixtures/ripley/spike_summary.json`)**: every probe in the ladder
returned HTTP 200 with no bot-challenge markers — decision rule row 1
("Any probe returns 200 -> `HttpxTransport`", design.md "Ripley Spike").
The result was unambiguous, not the "403 everywhere, no markers" geo/IP case,
so **`PlaywrightTransport` was not built** — there is nothing to gate behind
it. The `Transport` protocol below is still the seam design decision #5
describes, so a browser-backed transport could be added later without
changing `RipleyAdapter`'s constructor shape or the `StoreAdapter` port.

Probe 5 (`/api/catalog_system/pub/products/search?ft=pokemon`) answered 404,
not 200: Ripley is not VTEX, so `vtex.parse_products` is not reused. Ripley
serves a Next.js page embedding its product list as JSON in a
`<script id="__NEXT_DATA__">` tag (verified against a live, saved fixture);
`parse_search_page` reads that embedded JSON rather than scraping visible
HTML card markup, which is both what "HTML uses a Ripley card parser" cashes
out to for this store and materially more robust than CSS-selector scraping.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError, StoreParseError
from tracker.domain.matching import detect_language, detect_product_type, normalize
from tracker.domain.model import Availability, Money, Offer
from tracker.infrastructure.http.client import get_with_retry

STORE_SLUG = "ripley"
BASE_URL = "https://simple.ripley.com.pe"

#: Matches the actual number, ignoring whatever currency prefix precedes it
#: (see shopify.py's `_PRICE_NUMBER_RE` for why stripping non-digits instead
#: is unsafe — confirmed live on Pharmax's "S/. 99.90" format, a stray
#: prefix period colliding with the decimal point).
_PRICE_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")


def _clean_price(raw: str | None) -> Money | None:
    if not raw:
        return None
    match = _PRICE_NUMBER_RE.search(raw)
    if match is None:
        return None
    cleaned = match.group(0).replace(",", "")
    try:
        return Money(amount=Decimal(cleaned), currency="PEN")
    except InvalidOperation as exc:
        raise StoreParseError(f"ripley: invalid price value: {raw!r}") from exc


def _slugify(text: str) -> str:
    return _SLUG_INVALID_RE.sub("-", normalize(text)).strip("-")


def _product_url(base_url: str, name: str, sku: str) -> str:
    """Reconstructs Ripley's product URL shape (`/{slug}-{sku}p`), observed
    live in search-result anchors; the embedded product JSON carries no
    direct URL field."""
    return f"{base_url}/{_slugify(name)}-{sku}p"


def parse_search_page(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: the `/search/{query}` HTML page -> `Offer`s, via its embedded
    `__NEXT_DATA__` JSON."""
    tree = HTMLParser(body)
    script_node = tree.css_first("script#__NEXT_DATA__")
    if script_node is None:
        raise StoreParseError("ripley: __NEXT_DATA__ script not found in search page")

    try:
        data = json.loads(script_node.text())
        products = data["props"]["pageProps"]["findabilityProps"]["data"]["products"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StoreParseError(f"ripley: unexpected __NEXT_DATA__ shape: {exc}") from exc

    offers: list[Offer] = []
    for product in products:
        try:
            sku = str(product["sku"])
            name = str(product["name"])
            in_stock = bool(product.get("inStock"))
        except (KeyError, TypeError) as exc:
            # One malformed entry must not lose every other valid product on
            # the same page (same reasoning as vtex.py's parse_products).
            print(f"ripley: skipping malformed product entry: {exc}", file=sys.stderr)
            continue

        # Marketplace listings (Ripley's `seller` field — confirmed live,
        # 2026-09-28: "RIPLEY" for direct sales, "MARKETPLACE" for
        # third-party resellers) are routinely priced far above Ripley's own
        # price for the same kind of item (observed: S/789-1659 marketplace
        # vs S/289.90 Ripley-direct for the same ETB). User-requested filter:
        # only Ripley's own direct sales are worth tracking here. This is a
        # routine, expected filter (not an anomaly), so it isn't logged like
        # a malformed entry is.
        if product.get("seller") == "MARKETPLACE":
            continue

        offers.append(
            Offer(
                store=STORE_SLUG,
                external_id=sku,
                title=name,
                url=_product_url(base_url, name, sku),
                price=_clean_price(product.get("price")),
                availability=Availability.IN_STOCK if in_stock else Availability.OUT_OF_STOCK,
                language=detect_language(name),
                product_type=detect_product_type(name),
                observed_at=observed_at,
            )
        )
    return offers


def parse_product_detail_stock(body: str) -> int | None:
    """Pure: a product detail page's embedded `__NEXT_DATA__` ->
    `xcatentryQuantity` (the real unit count, confirmed live 2026-09-28 —
    search results only carry a boolean `inStock`, which is index data that
    can lag reality; this per-product page is Ripley's ground truth).

    Returns `None` (not an exception) on anything unparseable — this is a
    best-effort confirmation step, not the required happy path, so the
    caller decides how to treat "could not confirm" rather than this
    function forcing a hard failure."""
    tree = HTMLParser(body)
    script_node = tree.css_first("script#__NEXT_DATA__")
    if script_node is None:
        return None
    try:
        data = json.loads(script_node.text())
        product = data["props"]["pageProps"]["detailProps"]["data"]["product"]
        quantity = product.get("xcatentryQuantity")
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return int(quantity) if isinstance(quantity, int | float) else None


def _parse_known_product_detail(body: str, sku: str, url: str, observed_at: datetime) -> Offer:
    """Pure: a product detail page's `__NEXT_DATA__` -> one `Offer`, for
    `SupportsKnownProducts.fetch_known`.

    Honesty note (2026-09-29): `name`/`price` are read from the same
    `detailProps.data.product` object `parse_product_detail_stock` already
    reads `xcatentryQuantity` from — inferred by the same field-naming
    convention confirmed on the search page's product objects, but NOT
    independently confirmed live on the detail page itself: Ripley's
    Cloudflare was blocking this session's own IP for the entire time this
    was written. Raises `StoreParseError` on any unexpected shape rather
    than silently guessing wrong data into an offer — verify this against a
    real response once unblocked."""
    tree = HTMLParser(body)
    script_node = tree.css_first("script#__NEXT_DATA__")
    if script_node is None:
        raise StoreParseError(f"ripley: __NEXT_DATA__ script not found for known product {sku}")
    try:
        data = json.loads(script_node.text())
        product = data["props"]["pageProps"]["detailProps"]["data"]["product"]
        name = str(product["name"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StoreParseError(f"ripley: unexpected detail-page shape for known product {sku}: {exc}") from exc

    quantity = product.get("xcatentryQuantity")
    in_stock = isinstance(quantity, int | float) and quantity > 0

    return Offer(
        store=STORE_SLUG,
        external_id=sku,
        title=name,
        url=url,
        price=_clean_price(product.get("price")),
        availability=Availability.IN_STOCK if in_stock else Availability.OUT_OF_STOCK,
        language=detect_language(name),
        product_type=detect_product_type(name),
        observed_at=observed_at,
    )


class Transport(Protocol):
    """The seam design decision #5 describes: swapping transport never
    changes `RipleyAdapter`'s constructor shape or the `StoreAdapter` port."""

    def fetch_html(self, url: str) -> RawPayload: ...


class HttpxTransport:
    """Plain HTTP transport — confirmed sufficient by the Task 0 spike."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch_html(self, url: str) -> RawPayload:
        try:
            response = get_with_retry(self._client, url)
        except httpx.HTTPError as exc:
            raise StoreFetchError(f"ripley: request to {url} failed: {exc}") from exc
        return RawPayload(
            store=STORE_SLUG,
            source_url=url,
            content_type=response.headers.get("content-type", "text/html"),
            body=response.text,
            fetched_at=datetime.now(UTC),
        )


class PlaywrightTransport:
    """Browser-backed `Transport` — the seam the module docstring's Task 0
    spike anticipated but didn't need at the time (every probe returned 200
    with no bot-challenge markers). That changed: confirmed live 2026-09-29,
    Ripley now answers every page (not just search) with Cloudflare's
    `cf-mitigated: challenge` JS challenge, which `HttpxTransport` cannot
    pass — no plain HTTP client can execute JavaScript. A real (even
    headless) browser can.

    A fresh Playwright/browser instance is started and closed within each
    `fetch_html()` call, not reused across calls: Playwright's sync API is
    not thread-safe, and `TrackOffersUseCase` runs each store's fetch in its
    own worker thread — a shared instance built once in the main thread
    would violate that. The ~1-2s browser-launch cost per request is an
    acceptable tradeoff here, given the jittered ~20-30s interval this
    already runs under.

    Honesty note: confirmed live 2026-09-29 that this gets Playwright itself
    running end-to-end against the real Cloudflare challenge — but a fixed
    5s wait after navigation sometimes captured the challenge's "Just a
    moment..." interstitial instead of the real page (the challenge's own
    JS runs, sets a cookie, then reloads — how long that takes varies).
    Fixed by waiting for the specific element every page this adapter
    actually parses depends on, `script#__NEXT_DATA__`, instead of a blind
    sleep."""

    def fetch_html(self, url: str) -> RawPayload:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    # state="attached" (not the default "visible"): a
                    # <script> tag has no visual box, so waiting for
                    # "visible" would time out unconditionally regardless of
                    # whether the challenge actually resolved — confirmed
                    # live 2026-09-29, this exact bug.
                    page.wait_for_selector("script#__NEXT_DATA__", timeout=30000, state="attached")
                    body = page.content()
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise StoreFetchError(f"ripley: playwright request to {url} failed: {exc}") from exc

        return RawPayload(
            store=STORE_SLUG,
            source_url=url,
            content_type="text/html",
            body=body,
            fetched_at=datetime.now(UTC),
        )


class RipleyAdapter:
    store_slug = STORE_SLUG

    def __init__(self, transport: Transport, base_url: str = BASE_URL) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")

    def _search_url(self, query: str) -> str:
        # Verified live: a hyphenated slug (`pokemon-30-aniversario`) returns a
        # materially broader token-based match set than a literally
        # URL-encoded phrase for the same query on Ripley's search endpoint.
        slug = "-".join(query.split())
        return f"{self._base_url}/search/{quote(slug)}"

    def fetch(self, query: str) -> Sequence[RawPayload]:
        return [self._transport.fetch_html(self._search_url(query))]

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        offers: list[Offer] = []
        for payload in payloads:
            offers.extend(parse_search_page(payload.body, observed_at, self._base_url))
        return offers

    def _confirm_real_stock(self, offer: Offer) -> Offer:
        """Only called for offers search already reported IN_STOCK — keeps
        the extra per-product request proportional to genuine candidates,
        not the whole catalog. Fails closed: a confirmation that can't be
        obtained or doesn't show a positive quantity downgrades the offer to
        OUT_OF_STOCK rather than trusting the possibly-stale search-index
        boolean (user-requested: don't alert on search's word alone)."""
        try:
            payload = self._transport.fetch_html(offer.url)
        except StoreFetchError:
            return replace(offer, availability=Availability.OUT_OF_STOCK)
        quantity = parse_product_detail_stock(payload.body)
        if quantity is None or quantity <= 0:
            return replace(offer, availability=Availability.OUT_OF_STOCK)
        return offer

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        offers = self.parse(self.fetch(query), observed_at)
        return [
            self._confirm_real_stock(offer) if offer.availability is Availability.IN_STOCK else offer
            for offer in offers
        ]

    def fetch_known(self, identifiers: Sequence[str], observed_at: datetime) -> list[Offer]:
        """`SupportsKnownProducts`. Ripley uses TWO different URL shapes
        depending on whether the product has color/size variants — both
        confirmed live 2026-09-29:
          - a plain (no-variant) product resolves at `{slug}-{sku}p`
            (trailing "p"); this is the shape `_product_url` already builds
            for search-derived offers, and the descriptive slug is cosmetic
            (an arbitrary placeholder still resolves).
          - a product WITH color/size variants 404s on that "p" form and
            instead resolves at `{slug}-{sku}` (no "p") — the slug and any
            `?color_XX=...` query string are both optional there too.
        There's no way to tell which shape a given SKU needs without trying,
        so this attempts the "p" form first and falls back to the no-"p"
        form on failure. Fetches the detail page directly, which already
        carries the real stock quantity (`xcatentryQuantity`) — no separate
        confirmation round-trip needed, unlike `search()`."""
        offers: list[Offer] = []
        for sku in identifiers:
            url = f"{self._base_url}/producto-{sku}p"
            try:
                payload = self._transport.fetch_html(url)
            except StoreFetchError:
                url = f"{self._base_url}/producto-{sku}"
                try:
                    payload = self._transport.fetch_html(url)
                except StoreFetchError as exc:
                    print(f"ripley: skipping known product {sku}: {exc}", file=sys.stderr)
                    continue
            try:
                offers.append(_parse_known_product_detail(payload.body, sku, url, observed_at))
            except StoreParseError as exc:
                print(f"ripley: skipping known product {sku}: {exc}", file=sys.stderr)
        return offers
