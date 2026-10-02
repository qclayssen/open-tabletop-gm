"""Shared ASCII dash-slug convention for engine identifiers and filenames."""

import re


def slug(value: object) -> str:
    """Lowercase, replace each run outside ASCII a-z/0-9 with one dash.

    Non-ASCII letters and punctuation are separators, not transliterated.
    Inputs with no ASCII alphanumeric characters produce the empty string.
    """
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
