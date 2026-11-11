"""Plain-text previews sized in UTF-16 code units."""

from aiogram_extensions._validation import ConfigurationError, positive_integer, require_instance


def truncate_text(text: str, *, max_units: int, suffix: str = "…") -> str:
    """Fit plain text and a truncation marker in a UTF-16 code-unit budget.

    This preserves code points, not grapheme clusters. Apply before HTML/Markdown
    escaping and entity construction; existing markup and entities are not supported.
    """
    require_instance("text", text, str)
    require_instance("suffix", suffix, str)
    positive_integer("max_units", max_units)

    suffix_size = len(suffix.encode("utf-16-le")) // 2
    if suffix_size > max_units:
        raise ConfigurationError("suffix", "must fit in max_units")

    budget = max_units - suffix_size
    if text.isascii():
        return text if len(text) <= max_units else text[:budget] + suffix

    encoded = text.encode("utf-16-le")
    if len(encoded) // 2 <= max_units:
        return text
    # Ignoring only an incomplete trailing surrogate preserves whole code points.
    return encoded[: budget * 2].decode("utf-16-le", errors="ignore") + suffix
