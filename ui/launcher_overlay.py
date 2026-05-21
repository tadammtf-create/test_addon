"""Floating Launcher overlay drawn over the 3D Viewport.

This module owns the ``draw_handler_add`` registration that paints the
floating launcher button on top of every 3D Viewport. The handler is the
only Blender-supported way to draw a "floating" rectangle on top of the
viewport, and is registered against ``bpy.types.SpaceView3D`` with
``region_type='WINDOW'`` and ``draw_type='POST_PIXEL'`` so coordinates are
interpreted in pixel space (matching Requirements 1.1, 1.3, 1.5 — the
button is sized in pixels and positioned from the bottom-left of the
active region).

The draw handler is intentionally tolerant: every public entry point
catches its own exceptions and either logs and continues or returns a
safe default. Blender silently deregisters draw handlers that raise,
which would leave the launcher invisible until the addon is reloaded —
hence the top-level try/except in :func:`_draw_callback` and the
defensive area-type check (Requirement 1.10) even though we only
register against the View 3D space.

Module-level state (``_draw_handle``, ``_settings``, ``_icon``) is the
simplest shape for a function-based draw handler. Both :func:`register`
and :func:`unregister` are idempotent so the addon can reload without
double-adding the handler or failing when it has already been removed.

Validates: Requirements 1.1, 1.3, 1.4, 1.5, 1.6, 1.8, 1.9, 1.10, 12.2,
12.6.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d

from ..services.settings.store import SettingsStore
from ..services.settings.schema import (
    LAUNCHER_OFFSET_X,
    LAUNCHER_OFFSET_Y,
    LAUNCHER_OVERLAY_ENABLED,
    LAUNCHER_CUSTOM_ICON,
    THEME_SELECTION,
)
from .theme import load_theme, ThemeTokens  # noqa: F401  (ThemeTokens re-exported for type hints)
from .icons import LauncherIcon


# Single addon-wide logger; the same handle the service layer configures.
logger = logging.getLogger("ai_toolkit")

# Pixel size of the launcher button. Matches Requirement 1.1's literal
# "32 pixels" and is consumed by both the drawing code below and the
# click router in ui/operators/launcher_ops.py via :func:`get_launcher_rect`.
LAUNCHER_BUTTON_SIZE: int = 32


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
#
# Class attributes would also work, but a small set of module-level globals
# is the simpler shape for a plain function-based draw handler. The handler
# is added/removed by :func:`register` / :func:`unregister`; the bound
# settings store and cached icon are populated by :func:`bind_settings`
# from ``__init__.register()`` (task 24.1).

_draw_handle: Optional[object] = None
_settings: Optional[SettingsStore] = None
_icon: Optional[LauncherIcon] = None

# Rate-limit the per-draw error log so a misbehaving callback can't flood
# the console at viewport refresh rate. Reset to zero on each successful
# unregister so a reloaded addon starts fresh.
_draw_error_count: int = 0
_DRAW_ERROR_LOG_LIMIT: int = 5

# Separate rate-limit counter for the icon-GPU-texture failure path. The
# icon failure is inside the draw loop and could otherwise log at every
# viewport redraw (~60 Hz) when ``gpu.texture.from_image`` raises, which
# would flood the console and degrade interactive performance. Reset on
# ``unregister`` and ``bind_settings`` so a rebind (e.g. user changed the
# custom icon path) gets a fresh chance to surface a real warning.
_icon_error_count: int = 0
_ICON_ERROR_LOG_LIMIT: int = 3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def bind_settings(settings: SettingsStore) -> None:
    """Bind the :class:`SettingsStore` the draw handler reads each frame.

    Called by ``__init__.register()`` (task 24.1) before :func:`register`.
    Triggers a one-time icon load so the icon image is cached across
    draws (Requirement 1.6, 1.13, 1.14, 12.6).

    Idempotent: calling this multiple times rebinds the store but only
    loads the icon once. The icon is reset by :func:`unregister`.

    Failures inside :meth:`LauncherIcon.load` are already swallowed by
    that loader (it descends a fallback chain and always returns a
    non-null icon). The wider try/except here is purely defensive: a
    Blender API regression in a future version must not stop the
    addon from registering.
    """
    global _settings, _icon, _icon_error_count
    _settings = settings
    # Rebind is a fresh attempt: reset the icon-failure rate limiter so
    # a transient earlier failure does not silence a real new one.
    _icon_error_count = 0
    if _icon is None:
        try:
            custom = settings.get(LAUNCHER_CUSTOM_ICON) or ""
            _icon = LauncherIcon.load(custom if custom else None)
        except Exception as exc:  # noqa: BLE001 -- defensive guard
            logger.warning(
                "Launcher icon load failed during bind_settings: %s: %s; "
                "the launcher will draw without an icon.",
                type(exc).__name__,
                exc,
            )
            _icon = None


def get_launcher_rect(settings: SettingsStore) -> Tuple[int, int, int, int]:
    """Return ``(x, y, w, h)`` of the launcher button in pixel space.

    The origin is the bottom-left of the active 3D Viewport region, which
    matches the coordinate system used by ``draw_handler_add`` with
    ``draw_type='POST_PIXEL'``. The width and height are always
    :data:`LAUNCHER_BUTTON_SIZE` (Requirement 1.1).

    The horizontal and vertical offsets come from
    :data:`LAUNCHER_OFFSET_X` / :data:`LAUNCHER_OFFSET_Y` and are already
    clamped to ``[0, 4096]`` by :class:`SettingsStore` on read
    (Requirement 1.2). This function is also used by the click router
    (task 12.2) for hit-testing, so the rectangle returned here must be
    the same rectangle that gets drawn.
    """
    x = int(settings.get(LAUNCHER_OFFSET_X) or 0)
    y = int(settings.get(LAUNCHER_OFFSET_Y) or 0)
    return (x, y, LAUNCHER_BUTTON_SIZE, LAUNCHER_BUTTON_SIZE)


def register() -> None:
    """Register the draw handler. Idempotent.

    Adds a ``POST_PIXEL`` ``WINDOW``-region draw handler on
    ``bpy.types.SpaceView3D``. If the handler is already registered
    (``_draw_handle is not None``) this is a no-op so a Blender addon
    reload doesn't double-add. On failure (Blender API regression,
    permissions, etc.) the handler is left unset and the failure is
    logged so the rest of the addon can still register (Requirement
    1.10's defensive posture, Requirement 12.9's "register must not
    abort").
    """
    global _draw_handle
    if _draw_handle is not None:
        return
    try:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), 'WINDOW', 'POST_PIXEL'
        )
    except Exception as exc:  # noqa: BLE001 -- never abort addon register
        logger.warning(
            "Failed to register launcher draw handler (%s: %s); "
            "the floating launcher will not be visible this session.",
            type(exc).__name__,
            exc,
        )
        _draw_handle = None


def unregister() -> None:
    """Remove the draw handler. Idempotent and safe at any time.

    If the handler isn't registered (``_draw_handle is None``) this is a
    no-op. Otherwise it calls
    ``bpy.types.SpaceView3D.draw_handler_remove`` and clears the module
    state. Removal is wrapped in try/except because Blender may have
    already removed the handler during shutdown — re-removing a missing
    handler raises in some Blender versions, which would block the
    addon's ``unregister()`` and leave Blender in a weird state.

    The cached icon and rate-limit counter are also reset so a
    subsequent ``register()`` starts from a clean slate.
    """
    global _draw_handle, _icon, _draw_error_count, _icon_error_count
    if _draw_handle is None:
        return
    try:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
    except Exception as exc:  # noqa: BLE001 -- shutdown may have removed it
        logger.warning(
            "Failed to remove launcher draw handler (%s: %s); "
            "Blender may have already removed it during shutdown.",
            type(exc).__name__,
            exc,
        )
    finally:
        _draw_handle = None
        _icon = None
        _draw_error_count = 0
        _icon_error_count = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hex_to_rgba(
    hex_str: str, alpha: float = 0.95
) -> Tuple[float, float, float, float]:
    """Convert ``"#rrggbb"`` (or ``"#rgb"``) to ``(r, g, b, a)`` floats.

    Returns a neutral 50% grey on any malformed input so a corrupted
    theme value can never abort the draw. ``alpha`` defaults to ``0.95``
    so the launcher background is slightly translucent over the
    viewport — consistent with the rest of Blender's overlay surfaces.
    """
    s = hex_str.lstrip("#") if isinstance(hex_str, str) else ""
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (0.5, 0.5, 0.5, alpha)
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        return (0.5, 0.5, 0.5, alpha)
    return (r, g, b, alpha)


def _draw_filled_rect(
    shader: object,
    x: int,
    y: int,
    w: int,
    h: int,
    rgba: Tuple[float, float, float, float],
) -> None:
    """Draw an axis-aligned filled rectangle with the given UNIFORM_COLOR shader."""
    verts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    indices = [(0, 1, 2), (2, 3, 0)]
    batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", rgba)
    batch.draw(shader)


def _draw_callback() -> None:
    """The draw handler itself. Runs on every viewport redraw.

    Skips early when:

    * the overlay setting is disabled (Requirement 1.9)
    * the active area is not ``VIEW_3D`` (Requirement 1.10 defensive
      check — registering against ``SpaceView3D`` should make this
      redundant, but a misbehaving downstream addon could swap the
      context)
    * no :class:`SettingsStore` is bound (initialisation race during
      ``register()``)

    The entire body is wrapped in a try/except: Blender silently
    deregisters draw handlers that raise, which would leave the
    launcher invisible until the addon is reloaded.
    """
    global _draw_error_count, _icon_error_count
    try:
        # Initialisation race: ``register()`` may have run before
        # ``bind_settings()``. Skip silently — the next redraw will pick
        # up the bound store.
        if _settings is None:
            return

        # Requirement 1.9: hide entirely when the overlay setting is off.
        if not _settings.get(LAUNCHER_OVERLAY_ENABLED):
            return

        # Requirement 1.10: defensive area-type check. We register
        # against SpaceView3D so this should always be true here, but
        # ``bpy.context.area`` can be ``None`` during startup, file
        # load, or when called from a non-window draw context, and a
        # misbehaving downstream addon could push a non-VIEW_3D area
        # into the context.
        area = bpy.context.area
        if area is None or area.type != 'VIEW_3D':
            return

        # Resolve the rectangle (Requirements 1.1, 1.3, 1.5).
        x, y, w, h = get_launcher_rect(_settings)

        # Resolve theme tokens. ``load_theme`` never raises — on any
        # failure it logs and returns built-in defaults (Requirement
        # 12.9, 12.10). Re-resolving every draw is what makes the
        # ``match_blender_theme`` selection track live theme changes
        # without an addon reload.
        theme = load_theme(_settings.get(THEME_SELECTION))
        bg_rgba = _hex_to_rgba(theme.bg_primary, alpha=0.95)
        accent_rgba = _hex_to_rgba(theme.accent, alpha=1.0)

        # Enable ALPHA blending so the translucent background (alpha
        # 0.95) and PNG transparency composite correctly over the
        # viewport. The previous state is saved and restored after the
        # icon draw so we never leak GPU state into other draw handlers
        # (Blender docs: draw_texture_2d / blend_set).
        prev_blend = gpu.state.blend_get()
        gpu.state.blend_set('ALPHA')
        try:
            # Background quad (Requirements 1.4, 12.2 — theme-derived bg).
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            _draw_filled_rect(shader, x, y, w, h, bg_rgba)

            # Icon: 4 px inset on every side so the background reads as
            # a frame around the icon. The icon is loaded once at
            # ``bind_settings`` time and cached in ``_icon``.
            icon_drawn = False
            inset = 4
            if _icon is not None and getattr(_icon, "image", None) is not None:
                try:
                    # ``gpu.texture.from_image(image)`` is the
                    # documented Blender 3.2+/4.x API for obtaining a
                    # GPUTexture backed by a ``bpy.types.Image``.
                    # ``bpy.types.Image`` itself has no ``gpu_texture``
                    # attribute, which is why the previous call always
                    # raised AttributeError and silently fell back to
                    # the accent square.
                    # https://docs.blender.org/api/current/gpu.texture.html
                    texture = gpu.texture.from_image(_icon.image)
                    # ``is_scene_linear_with_rec709_srgb_target=True`` is
                    # the documented setting for drawing a
                    # ``bpy.types.Image`` texture inside a POST_PIXEL
                    # SpaceView3D draw handler — without it the PNG
                    # comes out washed out / wrongly gamma-corrected.
                    draw_texture_2d(
                        texture,
                        (x + inset, y + inset),
                        w - 2 * inset,
                        h - 2 * inset,
                        is_scene_linear_with_rec709_srgb_target=True,
                    )
                    icon_drawn = True
                except Exception as exc:  # noqa: BLE001 -- GPU texture may fail
                    # Rate-limited: the draw handler fires on every
                    # viewport redraw, so an unconditional warning here
                    # would flood Blender's console at refresh rate.
                    # Log the first few occurrences at WARNING so a real
                    # problem is visible, then go silent until the next
                    # ``bind_settings`` or ``unregister`` clears the
                    # counter.
                    if _icon_error_count < _ICON_ERROR_LOG_LIMIT:
                        logger.warning(
                            "Launcher icon GPU texture unavailable (%s: %s); "
                            "drawing accent fallback square.",
                            type(exc).__name__,
                            exc,
                        )
                    _icon_error_count += 1

            # Fallback: a solid accent-coloured square. Required so the
            # launcher button is never blank when the icon's GPU texture
            # is not ready (Requirements 1.13, 1.14, 12.5).
            if not icon_drawn:
                _draw_filled_rect(
                    shader,
                    x + inset,
                    y + inset,
                    w - 2 * inset,
                    h - 2 * inset,
                    accent_rgba,
                )
        finally:
            # Restore the prior blend mode so we don't leak state into
            # other draw handlers running on the same redraw pass.
            gpu.state.blend_set(prev_blend)

    except Exception as exc:  # noqa: BLE001 -- never crash Blender's draw loop
        if _draw_error_count < _DRAW_ERROR_LOG_LIMIT:
            logger.exception(
                "Launcher draw handler raised (%s: %s); suppressing further "
                "log records to avoid flooding the console.",
                type(exc).__name__,
                exc,
            )
        _draw_error_count += 1


__all__ = [
    "register",
    "unregister",
    "bind_settings",
    "get_launcher_rect",
    "LAUNCHER_BUTTON_SIZE",
]
