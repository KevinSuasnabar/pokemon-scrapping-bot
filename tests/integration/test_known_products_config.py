"""`load_known_products` — real file I/O on `tmp_path`."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracker.infrastructure.config.known_products import load_known_products


def test_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert load_known_products(tmp_path / "does_not_exist.json") == {}


def test_loads_a_valid_file(tmp_path: Path) -> None:
    config_path = tmp_path / "known_products.json"
    config_path.write_text('{"ripley": ["123"], "tailoy": ["https://x/y.html"]}', encoding="utf-8")

    known_products = load_known_products(config_path)

    assert known_products == {"ripley": ["123"], "tailoy": ["https://x/y.html"]}


def test_coerces_non_string_identifiers_to_strings(tmp_path: Path) -> None:
    """A numeric SKU typed without quotes in the JSON must still work — the
    file is hand-edited, this is an easy typo to make."""
    config_path = tmp_path / "known_products.json"
    config_path.write_text('{"plaza_vea": [102340715]}', encoding="utf-8")

    known_products = load_known_products(config_path)

    assert known_products == {"plaza_vea": ["102340715"]}


def test_invalid_json_raises_value_error(tmp_path: Path) -> None:
    config_path = tmp_path / "known_products.json"
    config_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        load_known_products(config_path)


def test_non_object_json_raises_value_error(tmp_path: Path) -> None:
    config_path = tmp_path / "known_products.json"
    config_path.write_text('["not", "an", "object"]', encoding="utf-8")

    with pytest.raises(ValueError):
        load_known_products(config_path)


def test_non_list_value_raises_value_error(tmp_path: Path) -> None:
    config_path = tmp_path / "known_products.json"
    config_path.write_text('{"ripley": "should-be-a-list"}', encoding="utf-8")

    with pytest.raises(ValueError):
        load_known_products(config_path)
