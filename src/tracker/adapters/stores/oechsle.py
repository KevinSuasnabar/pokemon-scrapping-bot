"""Oechsle (oechsle.pe) — config over `VtexStoreAdapter`."""

from __future__ import annotations

import httpx

from tracker.adapters.stores.vtex import VtexStoreAdapter

STORE_SLUG = "oechsle"
BASE_URL = "https://www.oechsle.pe"


class OechsleAdapter(VtexStoreAdapter):
    def __init__(self, client: httpx.Client) -> None:
        super().__init__(store_slug=STORE_SLUG, base_url=BASE_URL, client=client)
