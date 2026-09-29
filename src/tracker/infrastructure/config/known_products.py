"""Loads `known_products.json` — the user-curated "ghost product" list
(see `application.ports.SupportsKnownProducts`): a manually-added
{store_slug: [identifier, ...]} map for products confirmed real and
purchasable by direct URL/SKU but not surfaced by the store's own search.

Optional by design: a missing file means "no known products configured",
not an error — most stores never need this. A malformed file, however,
fails loud (the user hand-edits this file, so a typo should be caught
immediately, not silently ignored)."""

from __future__ import annotations

import json
from pathlib import Path


def load_known_products(path: str | Path) -> dict[str, list[str]]:
    file_path = Path(path)
    if not file_path.exists():
        return {}

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file_path}: invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{file_path}: expected a JSON object mapping store slug -> identifiers")

    known_products: dict[str, list[str]] = {}
    for store_slug, identifiers in data.items():
        if not isinstance(store_slug, str) or not isinstance(identifiers, list):
            raise ValueError(
                f"{file_path}: expected {{'store_slug': ['identifier', ...]}}, "
                f"got {store_slug!r}: {identifiers!r}"
            )
        known_products[store_slug] = [str(identifier) for identifier in identifiers]
    return known_products
