"""Pure classification rules: language, product type, anniversary, noise, target filter.

Parametrized against real titles observed during exploration/spike (Plaza
Vea, Ilahui, Ripley live captures) plus explicit edge cases (design.md
"Testing Strategy").
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.domain.matching import (
    detect_language,
    detect_product_type,
    is_anniversary,
    is_non_tcg_noise,
    is_target_offer,
    normalize,
)
from tracker.domain.model import Availability, Language, Money, Offer, ProductType


def test_normalize_strips_accents_and_casefolds() -> None:
    assert normalize("Inglés") == normalize("ingles") == "ingles"


def test_normalize_collapses_whitespace() -> None:
    assert normalize("  Pokemon   TCG  ") == "pokemon tcg"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Colección de Figuritas Tech Sticker POKÉMON TCG 30.º Aniversario en Inglés", Language.ENGLISH),
        ("Pokemon TCG 30 Aniversario ETB En Inglés", Language.ENGLISH),
        ("Pokemon TCG 30 Aniversario Box en Inglés", Language.ENGLISH),
        ("Pokemon TCG 30th Anniversary Booster Box (ENG)", Language.ENGLISH),
        ("POKEMON TCG PITCH BOOST BUNDLE INGLES 10-10422-109", Language.ENGLISH),
        ("Pokemon TCG 30 Aniversario ETB En Español", Language.SPANISH),
        ("POKEMON TCG FIRST PARTNER S3 ESPAÑOL 10-10410-106", Language.SPANISH),
        ("Pokemon TCG Booster Box Latino", Language.SPANISH),
        ("Pokemon TCG 30 Aniversario Collection Box", Language.UNKNOWN),
        ("Peluche Pikachu Pokemon 30 Aniversario", Language.UNKNOWN),
    ],
)
def test_detect_language_from_title(title: str, expected: Language) -> None:
    assert detect_language(title) == expected


def test_detect_language_structured_attribute_wins_over_title() -> None:
    # Title carries no marker at all; the structured attribute alone decides.
    title = "Pokemon TCG 30 Aniversario Collection Box"
    attributes = {"Idioma": ["Inglés"]}
    assert detect_language(title, attributes) == Language.ENGLISH


def test_detect_language_attribute_key_is_case_insensitive() -> None:
    title = "Pokemon TCG 30 Aniversario Collection Box"
    attributes = {"IDIOMA": ["Español"]}
    assert detect_language(title, attributes) == Language.SPANISH


def test_detect_language_unrelated_attributes_fall_back_to_title() -> None:
    title = "Pokemon TCG 30 Aniversario ETB En Inglés"
    attributes = {"Color": ["Multicolor"], "Modelo": ["10-10449-120"]}
    assert detect_language(title, attributes) == Language.ENGLISH


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Pokemon TCG 30 Aniversario Elite Trainer Box En Español", ProductType.ETB),
        ("POKEMON TCG PITCH ELITE TRAINER INGLES 10-10416-111", ProductType.ETB),
        # "POKEMON TCG"-branded Tech Sticker Collection — `detect_product_type`
        # sees "Colección" and classifies COLLECTION_BOX; it's a legitimate
        # match end-to-end (see test_is_target_offer_true_for_* below).
        ("Colección de Figuritas Tech Sticker POKÉMON TCG 30.º Aniversario en Inglés", ProductType.COLLECTION_BOX),
        ("Pokemon TCG 30th Anniversary Booster Box (ENG)", ProductType.BOOSTER_BOX),
        ("POKEMON TCG PERF DISPLAY BOOST INGLES caja de sobres", ProductType.BOOSTER_BOX),
        ("Pokemon TCG 30 Aniversario Pack X3 Booster en Inglés", ProductType.BOOSTER_PACK),
        ("POKEMON TCG PERF TRIPACK BOOST INGLES 10-10375-108", ProductType.BOOSTER_PACK),
        ("Pokemon TCG 30 Aniversario Blister En Ingles", ProductType.BLISTER),
        ("Pokemon TCG 30 Aniversario Collection Box", ProductType.COLLECTION_BOX),
        ("Peluche Pikachu Pokemon 30 Aniversario", ProductType.OTHER),
        # Real Plaza Vea titles (live-captured, Phase 4): Spanish descriptive
        # text even on the English-language card variant.
        ("Caja de Entrenador Élite POKÉMON TCG 30.º Aniversario en Inglés", ProductType.ETB),
        ("Caja EX POKÉMON TCG 30.º Aniversario en Inglés", ProductType.COLLECTION_BOX),
        ("Colección con Póster POKÉMON TCG 30.º Aniversario en Inglés", ProductType.COLLECTION_BOX),
        # Real Ripley titles (live-captured, user-reported 2026-09-18): same
        # product line as Plaza Vea above, but phrased WITHOUT "colección" —
        # "sticker" alone must still resolve to a real product type.
        ("POKÉMON TCG 30.º ANIVERSARIO – TECH STICKER AMARILLO EN INGLÉS", ProductType.COLLECTION_BOX),
        ("POKÉMON TCG 30.º ANIVERSARIO – PÓSTER COLECCIONABLE EN INGLÉS", ProductType.COLLECTION_BOX),
        ("POKÉMON TCG 30.º ANIVERSARIO – EX BOX AZUL EN INGLÉS", ProductType.COLLECTION_BOX),
        ("POKÉMON TCG 30.º ANIVERSARIO – ELITE TRAINER BOX (ETB) EN INGLÉS", ProductType.ETB),
    ],
)
def test_detect_product_type(title: str, expected: ProductType) -> None:
    assert detect_product_type(title) == expected


def test_is_non_tcg_noise_does_not_flag_tcg_branded_sticker_collection() -> None:
    """Regression: "sticker" alone must not be treated as merch noise — the
    real "Tech Sticker Collection" is sold under the "POKEMON TCG" brand."""
    assert is_non_tcg_noise("Colección de Figuritas Tech Sticker Pokemon 30 Aniversario") is False


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Pokemon TCG 30 Aniversario ETB En Inglés", True),
        ("Pokemon TCG 30th Anniversary Booster Box (ENG)", True),
        ("Pokemon TCG 30 Aniv Blister", True),
        ("Pokemon TCG Aniversario 30 Box", True),
        ("Pokemon Center Bolsa De Papel 25 Aniversario Japon", False),
        ("Pokemon TCG Prismatic Sobre Español", False),
    ],
)
def test_is_anniversary(title: str, expected: bool) -> None:
    assert is_anniversary(title) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Peluche Pikachu Pokemon 30 Aniversario", True),
        ("TOMY Pokemon 025 Pikachu Mini Figures 20th Anniversary Set", True),
        ("Pokemon 30 Aniversario Llavero Metalico", True),
        ("Pokemon TCG 30 Aniversario ETB En Inglés", False),
    ],
)
def test_is_non_tcg_noise(title: str, expected: bool) -> None:
    assert is_non_tcg_noise(title) is expected


def _make_offer(
    *,
    title: str,
    language: Language,
    product_type: ProductType,
    availability: Availability = Availability.IN_STOCK,
) -> Offer:
    return Offer(
        store="plaza_vea",
        external_id="1",
        title=title,
        url="https://example.test/p",
        price=Money(amount=Decimal("99.90")),
        availability=availability,
        language=language,
        product_type=product_type,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_is_target_offer_true_for_english_anniversary_tcg() -> None:
    offer = _make_offer(
        title="Pokemon TCG 30 Aniversario ETB En Inglés",
        language=Language.ENGLISH,
        product_type=ProductType.ETB,
    )
    assert is_target_offer(offer) is True


def test_is_target_offer_false_for_spanish() -> None:
    offer = _make_offer(
        title="Pokemon TCG 30 Aniversario ETB En Español",
        language=Language.SPANISH,
        product_type=ProductType.ETB,
    )
    assert is_target_offer(offer) is False


def test_is_target_offer_false_for_non_tcg_noise() -> None:
    offer = _make_offer(
        title="Peluche Pikachu Pokemon 30 Aniversario En Inglés",
        language=Language.ENGLISH,
        product_type=ProductType.OTHER,
    )
    assert is_target_offer(offer) is False


def test_is_target_offer_false_for_non_anniversary_product() -> None:
    offer = _make_offer(
        title="Pokemon TCG Prismatic Booster Box English",
        language=Language.ENGLISH,
        product_type=ProductType.BOOSTER_BOX,
    )
    assert is_target_offer(offer) is False


def test_is_target_offer_true_for_tcg_branded_sticker_collection() -> None:
    """Regression (user-reported, 2026-09-18): a real Plaza Vea product,
    "Colección de Figuritas Tech Sticker", was silently excluded by an
    over-broad "sticker" noise-word match even though it's sold under the
    "POKEMON TCG" brand as part of the official 30th Anniversary lineup."""
    offer = _make_offer(
        title="Colección de Figuritas Tech Sticker POKÉMON TCG 30.º Aniversario en Inglés",
        language=Language.ENGLISH,
        product_type=ProductType.COLLECTION_BOX,
    )
    assert is_target_offer(offer) is True


def test_is_target_offer_true_for_ripley_sticker_phrasing_without_collection_word(
) -> None:
    """Regression (user-reported, 2026-09-18): Ripley phrases the same Tech
    Sticker product line as "TECH STICKER AMARILLO EN INGLÉS" — no
    "colección"/"caja"/"box" word at all, unlike Plaza Vea's phrasing. Runs
    the real `detect_language`/`detect_product_type` pipeline (not a
    manually-supplied product_type) since the original bug was specifically
    that `detect_product_type` fell through to OTHER for this exact title."""
    title = "POKÉMON TCG 30.º ANIVERSARIO – TECH STICKER AMARILLO EN INGLÉS"
    offer = _make_offer(
        title=title,
        language=detect_language(title),
        product_type=detect_product_type(title),
    )
    assert offer.product_type is ProductType.COLLECTION_BOX
    assert is_target_offer(offer) is True


def test_is_target_offer_true_for_spanish_descriptive_english_card_etb() -> None:
    """Live-captured Plaza Vea case: descriptive text is Spanish
    ("Caja de Entrenador Élite") but the card language marker is English."""
    offer = _make_offer(
        title="Caja de Entrenador Élite POKÉMON TCG 30.º Aniversario en Inglés",
        language=Language.ENGLISH,
        product_type=ProductType.ETB,
    )
    assert is_target_offer(offer) is True
