"""Pharmax (pharmax.com.pe) — config + theme-specific HTML fallback over
`ShopifyStoreAdapter` (see shopify.py for the shared JSON-path logic).

Confirmed live (2026-09-28): same Shopify `suggest.json` API as every other
Shopify store, but a completely different theme from Ilahui (Pharmax runs
the "Impulse" theme family — `.ProductItem`/`.ProductItem__Title` markup,
not Ilahui's `div[data-product-id]`), so the HTML fallback needed its own
parser rather than reusing Ilahui's.

Known gap (documented, not guessed): every Pokémon product on Pharmax was
in stock at investigation time, so no real "sold out" markup was ever
observed to build a selector from — the HTML-fallback path (only used when
suggest.json's *request itself* fails, which is rare) reports `UNKNOWN`
availability rather than guessing `IN_STOCK`. `UNKNOWN` is never treated as
"available" anywhere downstream (`is_in_stock()`), so this fails safe. Update
`_AVAILABILITY_SOLD_OUT_SELECTOR` once a real sold-out example is found.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from tracker.adapters.stores.shopify import ShopifyStoreAdapter, _absolute_url, _parse_price_string
from tracker.domain.matching import detect_language, detect_product_type
from tracker.domain.model import Availability, Offer

STORE_SLUG = "pharmax"
BASE_URL = "https://pharmax.com.pe"


def _external_id_from_href(href: str) -> str | None:
    """The card carries no numeric id (unlike Ilahui's `data-product-id`) —
    only the product handle in its URL (`/products/{handle}?...`)."""
    path = href.split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[-2] == "products":
        return parts[-1]
    return None


def parse_search_html(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: Pharmax's theme — the `search?q=...` HTML page -> `Offer`s
    (fallback path, used only when suggest.json's request itself fails).
    See module docstring for the `UNKNOWN`-availability caveat.
    """
    tree = HTMLParser(body)
    offers: list[Offer] = []
    seen_ids: set[str] = set()

    for card in tree.css(".ProductItem"):
        title_node = card.css_first(".ProductItem__Title a")
        price_node = card.css_first(".ProductItem__Price")
        if title_node is None or price_node is None:
            continue

        href = title_node.attributes.get("href") or ""
        external_id = _external_id_from_href(href)
        if not external_id or external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        title = title_node.text(strip=True)
        offers.append(
            Offer(
                store=STORE_SLUG,
                external_id=external_id,
                title=title,
                url=_absolute_url(base_url, href),
                price=_parse_price_string(STORE_SLUG, price_node.text(strip=True)),
                availability=Availability.UNKNOWN,
                language=detect_language(title),
                product_type=detect_product_type(title),
                observed_at=observed_at,
            )
        )
    return offers


class PharmaxAdapter(ShopifyStoreAdapter):
    def __init__(self, client: httpx.Client, base_url: str = BASE_URL) -> None:
        super().__init__(store_slug=STORE_SLUG, base_url=base_url, client=client)

    def parse_search_html(self, body: str, observed_at: datetime) -> list[Offer]:
        return parse_search_html(body, observed_at, self._base_url)
