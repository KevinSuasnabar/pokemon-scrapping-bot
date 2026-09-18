"""Plaza Vea (plazavea.com.pe) — config over `VtexStoreAdapter`."""

from __future__ import annotations

import httpx

from tracker.adapters.stores.vtex import VtexStoreAdapter

STORE_SLUG = "plaza_vea"
BASE_URL = "https://www.plazavea.com.pe"


class PlazaVeaAdapter(VtexStoreAdapter):
    def __init__(self, client: httpx.Client) -> None:
        super().__init__(store_slug=STORE_SLUG, base_url=BASE_URL, client=client)
