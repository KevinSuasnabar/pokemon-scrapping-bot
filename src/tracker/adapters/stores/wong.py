"""Wong (wong.pe) — config over `VtexStoreAdapter`.

Confirmed live, 2026-09-28: same VTEX catalog API as Plaza Vea/Oechsle
(`/api/catalog_system/pub/products/search`). `robots.txt` disallows the
legacy `/busca/*` HTML search page, NOT the JSON API this adapter actually
uses — no conflict. All sampled sellers were the store's own first-party
seller (`WongIO`, sellerId "1"); no marketplace reseller pattern found
(unlike Ripley/Falabella), so no extra filtering was added here. Confirmed
a genuinely separate catalog from Metro (different product/SKU ids for the
same product name), despite both being Cencosud-Peru VTEX storefronts.
"""

from __future__ import annotations

import httpx

from tracker.adapters.stores.vtex import VtexStoreAdapter

STORE_SLUG = "wong"
BASE_URL = "https://www.wong.pe"


class WongAdapter(VtexStoreAdapter):
    def __init__(self, client: httpx.Client) -> None:
        super().__init__(store_slug=STORE_SLUG, base_url=BASE_URL, client=client)
