"""Validation and geometry helpers for the AI Toolkit service layer.

This module provides four pure, bpy-free helpers used by both the UI
layer and the service layer to validate user input and compute simple
geometry:

* :func:`validate_text` - bounded-length, non-whitespace text validator.
* :func:`hit_test` - half-open rectangular hit test in pixel coordinates.
* :func:`fit_thumbnail` - aspect-preserving thumbnail sizing capped at
  256 px on the longer side.
* :func:`is_valid_url` - URL format validator restricted to ``http`` and
  ``https`` schemes.

Per Requirement 13.1 this module imports nothing from ``bpy``,
``bpy_extras``, ``mathutils``, ``bgl``, ``gpu``, ``bmesh``, or ``blf``.
URL parsing uses only :mod:`urllib.parse` from the standard library.

The helpers here cover Requirements 1.1, 4.1, 4.5, 5.9, 6.1, 6.8, 7.1,
8.2, and 11.5.
"""

from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["validate_text", "hit_test", "fit_thumbnail", "is_valid_url"]


def validate_text(s: str, min_len: int, max_len: int) -> bool:
    """Return ``True`` iff ``s`` is an in-range, non-whitespace string.

    The string is accepted only when ``min_len <= len(s) <= max_len``
    *and* ``s.strip() != ""`` (i.e., at least one non-whitespace
    character is present).

    This is the validator used by every text input in the addon:
    Text-to-3D prompts and AI Texturing prompts (1..1000), Render
    Preview prompts (1..2000), and AI Assistant messages (1..4000).

    Implements Requirements 4.1, 4.5, 6.1, 6.8, 7.1, 8.2 (text input
    validation); also referenced by Property 8.
    """
    if len(s) < min_len or len(s) > max_len:
        return False
    return s.strip() != ""


def hit_test(
    rect: tuple[int, int, int, int],
    point: tuple[int, int],
) -> bool:
    """Return ``True`` iff ``point`` lies inside ``rect``.

    ``rect`` is ``(x, y, w, h)`` with the origin at the bottom-left in
    pixel coordinates (matching ``draw_handler_add`` with
    ``draw_type='POST_PIXEL'``). ``point`` is ``(px, py)``.

    The interval is intentionally half-open: a point is inside iff
    ``x <= px < x + w AND y <= py < y + h``. This guarantees adjacent,
    non-overlapping rectangles partition the plane without ambiguity at
    shared edges.

    Implements Requirements 1.1 (launcher button hit area) and the
    Launcher Menu click routing in Requirements 2.5, 2.8; also
    referenced by Property 2.
    """
    x, y, w, h = rect
    px, py = point
    return (x <= px < x + w) and (y <= py < y + h)


def fit_thumbnail(w: int, h: int) -> tuple[int, int]:
    """Return aspect-preserving dimensions capped at 256 px per side.

    For inputs ``w, h >= 1``:

    * If ``max(w, h) <= 256`` the dimensions are returned unchanged.
    * Otherwise both dimensions are scaled by ``256 / max(w, h)`` and
      rounded to the nearest integer, with each dimension clamped to a
      minimum of 1 so a very thin source image cannot collapse to 0.

    The returned ``(w', h')`` always satisfies ``max(w', h') <= 256`` and
    preserves the aspect ratio within floating-point tolerance.

    Raises :class:`ValueError` if either dimension is below 1, so the
    UI-layer caller can surface a friendly error rather than divide by
    zero.

    Implements Requirement 5.9 (Image-to-3D thumbnail preview); also
    referenced by Property 16.
    """
    if w < 1 or h < 1:
        raise ValueError(
            f"fit_thumbnail requires w >= 1 and h >= 1, got w={w}, h={h}"
        )
    longest = max(w, h)
    if longest <= 256:
        return (w, h)
    scale = 256 / longest
    w_prime = max(1, round(w * scale))
    h_prime = max(1, round(h * scale))
    return (w_prime, h_prime)


def is_valid_url(u: str) -> bool:
    """Return ``True`` iff ``u`` is a well-formed ``http``/``https`` URL.

    Empty strings are rejected. The URL is parsed with
    :func:`urllib.parse.urlparse` and accepted only when the scheme is
    exactly ``http`` or ``https`` (case-insensitive, as ``urlparse``
    lower-cases it) and the netloc is non-empty. Anything else returns
    ``False``.

    This is the gate used before launching the user's web browser for
    the configured documentation URL.

    Implements Requirement 11.5 (URL validation before browser launch);
    also referenced by Property 21.
    """
    if not u:
        return False
    try:
        parsed = urlparse(u)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True
