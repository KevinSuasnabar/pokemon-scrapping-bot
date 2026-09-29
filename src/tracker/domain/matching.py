"""Pure classification rules shared by every store adapter and the use case.

One module, one fixture-driven test table (design decision #3): every store's
title heuristics are the same, so there is exactly one place to fix a
misclassification instead of four.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from tracker.domain.model import Language, Offer, ProductType

#: Structured "language" attribute keys seen across storefronts (VTEX
#: specification dicts and similar). Checked case-insensitively, in order,
#: before falling back to title-marker detection.
LANGUAGE_SPEC_KEYS: tuple[str, ...] = ("Idioma", "Language", "Lenguaje")

_ENGLISH_MARKERS: tuple[str, ...] = ("ingles", "english", "(eng)")
_SPANISH_MARKERS: tuple[str, ...] = ("espanol", "spanish", "(esp)", "latino")

#: Ordered longest-match-first table: first matching pattern group wins.
#: Peruvian storefronts write descriptive text in Spanish even for the
#: English-language card variant (only "en Inglés"/"(ENG)" marks the card's
#: language), so every rule needs a Spanish equivalent, confirmed live
#: against real Plaza Vea titles during Phase 4 ("Caja de Entrenador Élite"
#: = Elite Trainer Box, "Colección" = Collection).
_PRODUCT_TYPE_RULES: tuple[tuple[tuple[str, ...], ProductType], ...] = (
    (
        ("elite trainer box", "elite trainer", "etb", "entrenador elite", "caja de entrenador"),
        ProductType.ETB,
    ),
    (("booster box", "caja de sobres", "caja de boosters"), ProductType.BOOSTER_BOX),
    (("sobre", "booster", "pack"), ProductType.BOOSTER_PACK),
    (("blister",), ProductType.BLISTER),
    (
        # "sticker" on its own (not just paired with "colección"/"caja"/"box"):
        # Ripley phrases the same TCG-branded product as e.g. "TECH STICKER
        # AMARILLO EN INGLÉS" with no other collection-type word, which would
        # otherwise fall through to OTHER and be silently excluded — same
        # real product line as the Plaza Vea "Colección ... Tech Sticker"
        # case the user already confirmed they want included.
        #
        # "mini tin" (not bare "tin"): added ahead of the 2026-10-02 "30th
        # Celebration Mini Tin" release, confirmed via the official product
        # name search — bare "tin" would false-positive on any title
        # containing "Argentina" (`argen-TIN-a`), so the full two-word phrase
        # is required, same defensive pattern as "lata coleccionable" instead
        # of bare "lata" (would collide with "plata"/"plataforma").
        ("box", "collection", "coleccion", "caja", "sticker", "mini tin", "lata coleccionable"),
        ProductType.COLLECTION_BOX,
    ),
)

_ANNIVERSARY_MARKERS: tuple[str, ...] = (
    "30 aniversario",
    "30th anniversary",
    "30 aniv",
    "aniversario 30",
    # Plaza Vea/Oechsle write the Spanish ordinal "30.º Aniversario"; NFKD
    # decomposes "º" (masculine ordinal indicator) to a plain "o", confirmed
    # live against real product titles during Phase 4.
    "30.o aniversario",
    "30o aniversario",
    # "30th Celebration" is this collection's own official set name on some
    # retailers (confirmed live, 2026-09-28: an Oechsle listing titled
    # "...Legendary Birds 30th Celebration Pokémon TCG Inglés..." was silently
    # excluded — parsed fine, filtered out here). Deliberately paired with
    # "30", never bare "celebration": Pokémon TCG has a real, DIFFERENT
    # "Celebrations" set for the 25th anniversary — matching "celebration"
    # alone would wrongly pull that unrelated product in.
    "30th celebration",
    "30 celebration",
    "celebration 30",
    "30 celebracion",
    "celebracion 30",
)

_NON_TCG_NOISE_MARKERS: tuple[str, ...] = (
    "peluche",
    "plush",
    "figura",
    "figure",
    "llavero",
    "taza",
    "polo",
    "mochila",
    # NOT "sticker": Plaza Vea's "Colección de Figuritas Tech Sticker" is sold
    # under the "POKEMON TCG" brand as part of the official 30th Anniversary
    # TCG lineup (confirmed live against the real product, user-reported).
    # Excluding it here was a false positive — it's a real TCG-branded
    # collectible, not generic merch like a plush toy or keychain.
)


def normalize(text: str) -> str:
    """NFKD accent-strip + casefold + whitespace collapse.

    `normalize("Inglés")` and `normalize("ingles")` are equal.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def _any_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def detect_language(
    title: str, attributes: Mapping[str, Sequence[str]] | None = None
) -> Language:
    """A structured attribute (see `LANGUAGE_SPEC_KEYS`) wins over the title;
    otherwise fall back to title markers; otherwise `UNKNOWN`.
    """
    if attributes:
        lower_keys = {key.lower(): values for key, values in attributes.items()}
        for spec_key in LANGUAGE_SPEC_KEYS:
            values = lower_keys.get(spec_key.lower())
            if not values:
                continue
            joined = normalize(" ".join(values))
            if _any_marker(joined, _ENGLISH_MARKERS):
                return Language.ENGLISH
            if _any_marker(joined, _SPANISH_MARKERS):
                return Language.SPANISH

    normalized_title = normalize(title)
    if _any_marker(normalized_title, _ENGLISH_MARKERS):
        return Language.ENGLISH
    if _any_marker(normalized_title, _SPANISH_MARKERS):
        return Language.SPANISH
    return Language.UNKNOWN


def detect_product_type(title: str) -> ProductType:
    normalized_title = normalize(title)
    for patterns, product_type in _PRODUCT_TYPE_RULES:
        if _any_marker(normalized_title, patterns):
            return product_type
    return ProductType.OTHER


def is_anniversary(title: str) -> bool:
    return _any_marker(normalize(title), _ANNIVERSARY_MARKERS)


def is_non_tcg_noise(title: str) -> bool:
    return _any_marker(normalize(title), _NON_TCG_NOISE_MARKERS)


def is_target_offer(offer: Offer) -> bool:
    """The one place the use case decides "does this belong in the report".

    Adapters call `detect_language` / `detect_product_type` while building
    `Offer`s; only the use case applies this final filter (design decision #4).
    """
    return (
        offer.language is Language.ENGLISH
        and is_anniversary(offer.title)
        and offer.product_type is not ProductType.OTHER
        and not is_non_tcg_noise(offer.title)
    )
