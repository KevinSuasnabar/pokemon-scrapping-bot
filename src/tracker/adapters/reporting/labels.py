"""Spanish display labels for domain enums.

Presentation-only translation layer shared by every `Reporter` implementation.
The domain enums (`ChangeKind`, `Availability`) stay in English — they're the
program's internal contract, not user-facing copy (see CLAUDE.md persona
scope: artifacts/identifiers default to English, only reply/report text is
localized). Reporters map through here instead of printing `.value` directly.
"""

from __future__ import annotations

from tracker.domain.events import ChangeKind
from tracker.domain.model import Availability

_KIND_LABELS: dict[ChangeKind, str] = {
    ChangeKind.BASELINE: "línea base",
    ChangeKind.NEW: "NUEVO",
    ChangeKind.RESTOCKED: "REABASTECIDO",
    ChangeKind.PRICE_DROP: "BAJA DE PRECIO",
}

_KIND_EMOJI: dict[ChangeKind, str] = {
    ChangeKind.BASELINE: "📋",
    ChangeKind.NEW: "🆕",
    ChangeKind.RESTOCKED: "🔁",
    ChangeKind.PRICE_DROP: "💰",
}

_AVAILABILITY_LABELS: dict[Availability, str] = {
    Availability.IN_STOCK: "en stock",
    Availability.OUT_OF_STOCK: "agotado",
    Availability.UNKNOWN: "desconocido",
}


def kind_label(kind: ChangeKind) -> str:
    return _KIND_LABELS[kind]


def kind_emoji(kind: ChangeKind) -> str:
    return _KIND_EMOJI[kind]


def availability_label(availability: Availability | None) -> str:
    if availability is None:
        return _AVAILABILITY_LABELS[Availability.UNKNOWN]
    return _AVAILABILITY_LABELS[availability]
