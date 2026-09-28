"""Ilahui (ilahuiperu.com) — config + theme-specific HTML fallback over
`ShopifyStoreAdapter` (see shopify.py for the shared JSON-path logic)."""

from __future__ import annotations

from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from tracker.adapters.stores.shopify import ShopifyStoreAdapter, _absolute_url, _parse_price_string
from tracker.domain.matching import detect_language, detect_product_type
from tracker.domain.model import Availability, Offer

STORE_SLUG = "ilahui"
BASE_URL = "https://ilahuiperu.com"


def parse_search_html(body: str, observed_at: datetime, base_url: str = BASE_URL) -> list[Offer]:
    """Pure: Ilahui's theme — the `search?q=...` HTML page -> `Offer`s
    (fallback path, used only when suggest.json's request itself fails).

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
                price=_parse_price_string(STORE_SLUG, price_node.text(strip=True)),
                availability=Availability.IN_STOCK if is_available else Availability.OUT_OF_STOCK,
                language=detect_language(title),
                product_type=detect_product_type(title),
                observed_at=observed_at,
            )
        )
    return offers


class IlahuiAdapter(ShopifyStoreAdapter):
    def __init__(self, client: httpx.Client, base_url: str = BASE_URL) -> None:
        super().__init__(store_slug=STORE_SLUG, base_url=base_url, client=client)

    def parse_search_html(self, body: str, observed_at: datetime) -> list[Offer]:
        return parse_search_html(body, observed_at, self._base_url)
