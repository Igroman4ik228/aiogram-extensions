"""Plain-text previews respect Telegram's UTF-16 entity coordinate system."""

import pytest

from aiogram_extensions import utilities


def test_preview_truncates_with_a_suffix_without_splitting_astral_characters() -> None:
    assert utilities.truncate_text("A😀BCDEF", max_units=5) == "A😀B…"
    assert utilities.truncate_text("😀😀end", max_units=4) == "😀…"


@pytest.mark.parametrize(
    ("text", "units", "suffix", "expected"),
    [
        ("", 1, "…", ""),
        ("😀", 2, "…", "😀"),
        ("abc", 3, "…", "abc"),
        ("😀end", 1, "…", "…"),
        ("abcdef", 3, "", "abc"),
        ("😀end", 2, "", "😀"),
        ("abcdef", 3, "..", "a.."),
        ("abcdef", 2, "😀", "😀"),
    ],
)
def test_preview_boundaries(text: str, units: int, suffix: str, expected: str) -> None:
    assert utilities.truncate_text(text, max_units=units, suffix=suffix) == expected


@pytest.mark.parametrize("units", [0, -1, True, 1.5])
def test_preview_rejects_invalid_budget(units: int) -> None:
    with pytest.raises(ValueError, match="max_units"):
        utilities.truncate_text("text", max_units=units)


def test_preview_rejects_a_suffix_exceeding_the_budget() -> None:
    with pytest.raises(ValueError, match="suffix"):
        utilities.truncate_text("text", max_units=1, suffix="😀")
