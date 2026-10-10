"""Shared display validation. No expression/statistical calculation."""

import re
from PIL import ImageColor
from bulk_rnaseq.exceptions import SkillError


def validate_display(title: str, figure_width: int, figure_height: int) -> None:
    if (not isinstance(title, str) or len(title) > 160 or any(ord(c) < 32 for c in title)
            or any(type(n) is not int or not 600 <= n <= 3000 for n in (figure_width, figure_height))):
        raise SkillError("INVALID_PARAMETER", "Title must be plain text (<=160 characters); figure dimensions are integer pixels in [600,3000].")


def color_hex(value: str) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}|[A-Za-z]{1,30}", value):
            raise ValueError()
        rgb = ImageColor.getrgb(value)
        return "#%02X%02X%02X" % rgb
    except (ValueError, TypeError):
        raise SkillError("INVALID_PARAMETER", "Use a named CSS color or #RRGGBB.") from None


def validate_gene_labels(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    if (not isinstance(values, list) or len(values) > 200
            or any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 160
                   or any(ord(c) < 32 for c in v) for v in values) or len(values) != len(set(values))):
        raise SkillError("INVALID_PARAMETER", "Gene IDs must be unique, non-empty strings (at most 200).")
    return list(values)
