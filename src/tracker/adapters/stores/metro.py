"""Metro (metro.pe) — config over `VtexStoreAdapter`.

Confirmed live, 2026-09-28: same VTEX catalog API as Plaza Vea/Oechsle
(`/api/catalog_system/pub/products/search`). `robots.txt` disallows the
legacy `/buscapagina/*` HTML search page, NOT the JSON API this adapter
actually uses — no conflict. All sampled sellers were the store's own
first-party seller (`CENCOSUD RETAIL PERU S.A.`, sellerId "1"); no
marketplace reseller pattern found (unlike Ripley/Falabella), so no extra
filtering was added here.
"""

from __future__ import annotations

import httpx

from tracker.adapters.stores.vtex import VtexStoreAdapter

STORE_SLUG = "metro"
BASE_URL = "https://www.metro.pe"


class MetroAdapter(VtexStoreAdapter):
    def __init__(self, client: httpx.Client) -> None:
        super().__init__(store_slug=STORE_SLUG, base_url=BASE_URL, client=client)
