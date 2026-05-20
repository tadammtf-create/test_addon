"""ThemeTokens and the bpy-aware theme loader.

This module is part of the UI layer and is allowed to import ``bpy``,
but it does so lazily inside :func:`load_theme` so the module itself
can be imported in a plain Python interpreter (used by tooling and
smoke tests).

The loader supports three theme selections, mirroring the values stored
in :data:`services.settings.schema.THEME_SELECTION`:

* ``"dark"`` and ``"light"`` read tokens from the bundled
  ``ui/theme.json`` file. Any per-token entry that is missing or has a
  wrong type falls back to the matching built-in default.
* ``"match_blender_theme"`` derives colour tokens from
  ``bpy.context.preferences.themes[0]`` on every call so the UI tracks
  Blender theme changes without an addon reload (Requirement 12.10).

Loading never raises. Every failure path logs an error with the file
path or the offending field name plus the originating exception class
and returns a usable :class:`ThemeTokens` so addon registration always
succeeds (Requirements 12.9, 12.11).

Validates: Requirements 12.1, 12.2, 12.7, 12.8, 12.9, 12.10.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields as _dc_fields
from typing import Any

# Single addon-wide logger; the same handle the service layer uses.
logger = logging.getLogger("ai_toolkit")

# Absolute path of the bundled theme definition file. The loader reads
# this file for the "dark" and "light" selections (Requirement 12.1).
THEME_JSON_PATH = os.path.join(os.path.dirname(__file__), "theme.json")


@dataclass(frozen=True)
class ThemeTokens:
    """Immutable bag of theme tokens used by every custom-drawn UI surface.

    Colour tokens are 6-digit hex strings (``#rrggbb``). Spacing,
    corner radius, and font sizes are positive integers in pixels.
    Keeping every field a JSON-serialisable primitive lets the same
    dataclass round-trip cleanly through ``ui/theme.json`` and through
    the bpy-free service layer's request/response boundary.
    """

    bg_primary: str
    bg_secondary: str
    accent: str
    text_primary: str
    text_muted: str
    spacing_xs: int
    spacing_sm: int
    spacing_md: int
    spacing_lg: int
    corner_radius: int
    font_size_sm: int
    font_size_md: int
    font_size_lg: int


# Module-level set so unknown-theme-name warnings only fire once per
# process even when load_theme is called every draw.
_warned_unknown_themes: set[str] = set()


# Built-in defaults baked into the module so addon registration cannot
# fail when ``ui/theme.json`` is missing or unreadable (Requirement 12.9).
# Spacing, corner-radius, and font-size tokens are identical across
# light and dark; only colour tokens differ.
_BUILTIN_DEFAULTS: dict[str, "ThemeTokens"] = {
    "dark": ThemeTokens(
        bg_primary="#1f1f23",
        bg_secondary="#2a2a30",
        accent="#4f8cff",
        text_primary="#f1f1f5",
        text_muted="#a0a0a8",
        spacing_xs=2,
        spacing_sm=4,
        spacing_md=8,
        spacing_lg=16,
        corner_radius=4,
        font_size_sm=11,
        font_size_md=13,
        font_size_lg=16,
    ),
    "light": ThemeTokens(
        bg_primary="#f5f5f7",
        bg_secondary="#e8e8ea",
        accent="#2563eb",
        text_primary="#16161a",
        text_muted="#5b5b66",
        spacing_xs=2,
        spacing_sm=4,
        spacing_md=8,
        spacing_lg=16,
        corner_radius=4,
        font_size_sm=11,
        font_size_md=13,
        font_size_lg=16,
    ),
}


def _builtin_defaults_for(theme_name: str) -> ThemeTokens:
    """Return the built-in default :class:`ThemeTokens` for ``theme_name``.

    ``theme_name`` is expected to be ``"dark"`` or ``"light"``. On any
    other value the function logs a warning the first time that name is
    seen in the current process and returns the built-in dark defaults.
    """
    tokens = _BUILTIN_DEFAULTS.get(theme_name)
    if tokens is not None:
        return tokens
    if theme_name not in _warned_unknown_themes:
        _warned_unknown_themes.add(theme_name)
        logger.warning(
            "Unknown theme name %r; falling back to built-in dark defaults",
            theme_name,
        )
    return _BUILTIN_DEFAULTS["dark"]


def _coerce_token_value(field_name: str, raw: Any, default: Any) -> Any:
    """Return ``raw`` when its type matches ``default``; otherwise ``None``.

    Logs an error with the offending field name, the observed type, and
    :data:`THEME_JSON_PATH` when coercion fails. Booleans are explicitly
    rejected when an ``int`` is expected because ``bool`` subclasses
    ``int`` in Python.
    """
    expected_type = type(default)
    if isinstance(raw, bool) and expected_type is int:
        logger.error(
            "Theme token %r has wrong type %s in %s; using built-in default",
            field_name,
            type(raw).__name__,
            THEME_JSON_PATH,
        )
        return None
    if isinstance(raw, expected_type):
        return raw
    logger.error(
        "Theme token %r has wrong type %s in %s; using built-in default",
        field_name,
        type(raw).__name__,
        THEME_JSON_PATH,
    )
    return None


def _tokens_from_dict(payload: dict, defaults: ThemeTokens) -> ThemeTokens:
    """Build a :class:`ThemeTokens` from ``payload``, falling back to ``defaults``.

    Missing entries silently use the matching default; entries with the
    wrong type log an error (via :func:`_coerce_token_value`) and use
    the matching default.
    """
    values: dict[str, Any] = {}
    for field in _dc_fields(ThemeTokens):
        default_value = getattr(defaults, field.name)
        if field.name not in payload:
            values[field.name] = default_value
            continue
        coerced = _coerce_token_value(field.name, payload[field.name], default_value)
        values[field.name] = default_value if coerced is None else coerced
    return ThemeTokens(**values)


def _load_from_json(theme_name: str) -> ThemeTokens:
    """Read ``ui/theme.json`` and return tokens for ``theme_name``.

    Any of the following failure modes log an error including the file
    path and the originating exception class, then return the built-in
    default for ``theme_name``:

    * ``theme.json`` is missing
    * ``theme.json`` is not valid JSON or not valid UTF-8
    * the JSON root is not an object, or has no ``themes`` object, or
      has no entry for ``theme_name``
    * a per-token entry has a wrong type (handled in
      :func:`_tokens_from_dict`)
    """
    defaults = _builtin_defaults_for(theme_name)
    try:
        with open(THEME_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        logger.error(
            "Theme file %s not found (%s: %s); using built-in %s defaults",
            THEME_JSON_PATH,
            type(exc).__name__,
            exc,
            theme_name,
        )
        return defaults
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.error(
            "Failed to read theme file %s (%s: %s); using built-in %s defaults",
            THEME_JSON_PATH,
            type(exc).__name__,
            exc,
            theme_name,
        )
        return defaults

    if not isinstance(data, dict):
        logger.error(
            "Theme file %s root is %s, expected object; using built-in %s defaults",
            THEME_JSON_PATH,
            type(data).__name__,
            theme_name,
        )
        return defaults
    themes = data.get("themes")
    if not isinstance(themes, dict):
        logger.error(
            "Theme file %s missing 'themes' object; using built-in %s defaults",
            THEME_JSON_PATH,
            theme_name,
        )
        return defaults
    payload = themes.get(theme_name)
    if not isinstance(payload, dict):
        logger.error(
            "Theme file %s has no entry for theme %r; using built-in defaults",
            THEME_JSON_PATH,
            theme_name,
        )
        return defaults
    return _tokens_from_dict(payload, defaults)


def _color_to_hex(color: Any) -> str:
    """Convert a Blender RGB(A) colour (floats in [0, 1]) to ``#rrggbb``.

    Only the first three channels are used; alpha is dropped because
    :class:`ThemeTokens` colours are 6-digit hex. Channel values are
    clamped to ``[0.0, 1.0]`` before being scaled to 8-bit.
    """
    r = int(round(max(0.0, min(1.0, float(color[0]))) * 255))
    g = int(round(max(0.0, min(1.0, float(color[1]))) * 255))
    b = int(round(max(0.0, min(1.0, float(color[2]))) * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _load_from_blender_theme() -> ThemeTokens:
    """Derive :class:`ThemeTokens` from the active Blender theme.

    ``bpy`` is imported lazily so this module remains importable in a
    plain Python interpreter. When ``bpy`` is unavailable, or when any
    attribute traversal raises (the theme structure varies across
    Blender versions), this function logs a warning naming the
    exception class and returns the built-in dark defaults so addon
    registration never crashes (Requirements 12.9, 12.11).

    Spacing, corner radius, and font sizes always come from the
    built-in dark defaults because Blender's theme editor does not
    expose equivalent settings.
    """
    fallback = _builtin_defaults_for("dark")
    try:
        import bpy  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.warning(
            "bpy unavailable (%s: %s); using built-in dark defaults for "
            "match_blender_theme",
            type(exc).__name__,
            exc,
        )
        return fallback

    try:
        theme = bpy.context.preferences.themes[0]
        ui = theme.user_interface
        regular = ui.wcol_regular
        box = ui.wcol_box
        bg_primary = _color_to_hex(regular.inner)
        bg_secondary = _color_to_hex(box.inner)
        accent = _color_to_hex(regular.outline)
        text_primary = _color_to_hex(regular.text)
        # Some Blender versions expose a separate disabled-text colour;
        # fall back to the regular text colour when it is absent.
        muted_source = getattr(regular, "text_sel", None)
        if muted_source is None:
            muted_source = regular.text
        text_muted = _color_to_hex(muted_source)
    except Exception as exc:  # noqa: BLE001 -- theme structure varies per Blender version
        logger.warning(
            "Failed to derive ThemeTokens from active Blender theme "
            "(%s: %s); using built-in dark defaults",
            type(exc).__name__,
            exc,
        )
        return fallback

    return ThemeTokens(
        bg_primary=bg_primary,
        bg_secondary=bg_secondary,
        accent=accent,
        text_primary=text_primary,
        text_muted=text_muted,
        spacing_xs=fallback.spacing_xs,
        spacing_sm=fallback.spacing_sm,
        spacing_md=fallback.spacing_md,
        spacing_lg=fallback.spacing_lg,
        corner_radius=fallback.corner_radius,
        font_size_sm=fallback.font_size_sm,
        font_size_md=fallback.font_size_md,
        font_size_lg=fallback.font_size_lg,
    )


def load_theme(theme_selection: str) -> ThemeTokens:
    """Return the :class:`ThemeTokens` for ``theme_selection``.

    ``theme_selection`` is one of ``"dark"``, ``"light"``, or
    ``"match_blender_theme"`` (Requirement 12.7). For ``"dark"`` and
    ``"light"`` the loader reads ``ui/theme.json`` and falls back to
    built-in defaults on any error (Requirement 12.9). For
    ``"match_blender_theme"`` the loader derives colour tokens from
    ``bpy.context.preferences.themes[0]`` on every call so the UI
    tracks live theme changes without an addon reload (Requirement
    12.10).

    This function never raises: every failure path logs and returns a
    usable :class:`ThemeTokens` so addon registration cannot be aborted
    by a missing or malformed theme (Requirements 12.9, 12.11).
    """
    if theme_selection == "match_blender_theme":
        return _load_from_blender_theme()
    if theme_selection not in _BUILTIN_DEFAULTS:
        return _builtin_defaults_for(theme_selection)
    return _load_from_json(theme_selection)


__all__ = ["ThemeTokens", "load_theme"]
