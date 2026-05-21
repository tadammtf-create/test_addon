"""Bottom-left floating launcher button.

Restores the discoverable corner entry point the user is used to from
the previous design, but with three deliberate differences:

* The button is **deliberately small and unobtrusive** — a 32×32 px
  square with a low-alpha background and a 1-px accent border. No
  glow, no gradient, no oversized icon graphic.
* Clicking it opens the **clean Blender-native sidebar** (the new
  ``AITK_PT_root`` panel under the AI Toolkit tab), NOT the old
  GPU-drawn modal menu that opened upwards from the cursor and was
  what went off-screen in the previous design.
* Both the visibility and the position are user-controlled
  (``ui.show_launcher_button``, ``ui.launcher_offset_x`` /
  ``ui.launcher_offset_y``) so a user who wants the corner clean can
  hide the button entirely and still reach the addon through the
  header icon or by pressing ``N``.

The implementation follows Blender's standard floating-overlay pattern:
a ``POST_PIXEL`` draw handler on :class:`bpy.types.SpaceView3D` paints
the button in region-pixel space, and a click-router operator bound to
``LEFTMOUSE`` in the 3D View keymap hit-tests the cursor against the
same rectangle and dispatches to ``aitk.open_sidebar`` on a hit.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import bpy


logger = logging.getLogger("ai_toolkit")


# ----------------------------------------------------------------------
# Button geometry constants — small enough to read as a corner badge,
# big enough to hit comfortably with the mouse.
# ----------------------------------------------------------------------

BUTTON_SIZE: int = 32

# Accent → RGB lookup. Used to tint the button's 1-px border so the
# floating launcher reflects the user's chosen accent without us having
# to drag the full ThemeTokens loader in.
_ACCENT_RGB = {
    "BLUE": (0.31, 0.55, 1.00),
    "ORANGE": (1.00, 0.55, 0.20),
    "GREEN": (0.30, 0.85, 0.40),
    "PURPLE": (0.65, 0.45, 1.00),
    "PINK": (1.00, 0.45, 0.70),
    "YELLOW": (1.00, 0.85, 0.20),
    "NEUTRAL": (0.55, 0.55, 0.60),
}

# Background + text colours — picked to match Blender's standard dark
# overlay surfaces (e.g. the gizmo backgrounds) so the launcher reads
# as a first-party Blender widget rather than a custom web/sci-fi widget.
_BG_RGBA = (0.110, 0.110, 0.125, 0.78)
_TEXT_RGBA = (0.95, 0.95, 0.96, 0.98)


# ----------------------------------------------------------------------
# Module-level handles
# ----------------------------------------------------------------------

_draw_handle: Optional[object] = None
_keymap_entries: List[Tuple[object, object]] = []
_draw_error_count: int = 0
_DRAW_ERROR_LIMIT: int = 5


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _aitk_ui(context):
    """Return the ``ui`` PropertyGroup, or ``None`` if the addon isn't ready."""
    scene = getattr(context, "scene", None)
    root = getattr(scene, "aitk", None) if scene is not None else None
    return getattr(root, "ui", None) if root is not None else None


def _get_offsets(ui) -> Tuple[int, int]:
    """Return the user-configured offsets, clamped to a safe range."""
    if ui is None:
        return (20, 20)
    return (
        max(0, min(int(getattr(ui, "launcher_offset_x", 20)), 4096)),
        max(0, min(int(getattr(ui, "launcher_offset_y", 20)), 4096)),
    )


def get_button_rect(ui) -> Tuple[int, int, int, int]:
    """Return ``(x, y, w, h)`` of the launcher rectangle in region pixels."""
    ox, oy = _get_offsets(ui)
    return (ox, oy, BUTTON_SIZE, BUTTON_SIZE)


def _accent_rgb(ui) -> Tuple[float, float, float]:
    """Resolve the user's accent to an ``(r, g, b)`` triplet."""
    if ui is None:
        return _ACCENT_RGB["BLUE"]
    return _ACCENT_RGB.get(getattr(ui, "accent_color", "BLUE"), _ACCENT_RGB["BLUE"])


# ----------------------------------------------------------------------
# GPU draw helpers
# ----------------------------------------------------------------------


def _draw_filled_rect(shader, batch_for_shader, x, y, w, h, rgba):
    verts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", rgba)
    batch.draw(shader)


def _draw_line_rect(shader, batch_for_shader, x, y, w, h, rgba):
    verts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    batch = batch_for_shader(shader, "LINE_LOOP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", rgba)
    batch.draw(shader)


def _draw_callback() -> None:
    """Paint the launcher button on every 3D Viewport redraw."""
    global _draw_error_count
    try:
        import gpu  # noqa: F401 — lazy so non-Blender import paths don't fail
        import blf
        from gpu_extras.batch import batch_for_shader
    except Exception:
        return

    try:
        ui = _aitk_ui(bpy.context)
        if ui is None:
            return
        if not bool(getattr(ui, "show_launcher_button", True)):
            return

        area = bpy.context.area
        if area is None or area.type != "VIEW_3D":
            return

        x, y, w, h = get_button_rect(ui)
        accent = _accent_rgb(ui)

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        # Alpha-blended background + thin accent border. The blend mode
        # is restored to ``NONE`` afterwards so other draw handlers
        # downstream don't inherit our alpha state.
        gpu.state.blend_set("ALPHA")
        try:
            _draw_filled_rect(shader, batch_for_shader, x, y, w, h, _BG_RGBA)
            _draw_line_rect(
                shader, batch_for_shader, x, y, w, h, (*accent, 0.85)
            )

            # "AI" wordmark — small, centred, two characters wide so it
            # reads as identity rather than just an icon swatch.
            font_id = 0
            try:
                blf.size(font_id, 12)
            except Exception:
                # Older signatures take an explicit dpi argument.
                logger.debug("blf.size signature mismatch", exc_info=True)
            blf.color(font_id, *_TEXT_RGBA)
            # Roughly centre the "AI" glyph block inside the 32×32 box.
            blf.position(font_id, x + 8, y + 10, 0)
            blf.draw(font_id, "AI")
        finally:
            gpu.state.blend_set("NONE")
    except Exception as exc:  # noqa: BLE001 — never crash the draw loop
        if _draw_error_count < _DRAW_ERROR_LIMIT:
            logger.exception(
                "Launcher overlay draw raised (%s: %s)",
                type(exc).__name__,
                exc,
            )
        _draw_error_count += 1


# ----------------------------------------------------------------------
# Click router
# ----------------------------------------------------------------------


class AITK_OT_launcher_click_router(bpy.types.Operator):
    """Hit-test ``LEFTMOUSE`` against the launcher rect; pass through on miss.

    The operator is bound to ``LEFTMOUSE PRESS`` in the 3D View keymap
    by :func:`register_overlay`. Because Blender invokes every keymap
    entry in turn and the first one to return anything other than
    ``PASS_THROUGH`` wins the event, returning ``PASS_THROUGH`` on a
    miss lets every existing 3D Viewport interaction (object selection,
    gizmos, other addons) behave exactly as before.
    """

    bl_idname = "aitk.launcher_click_router"
    bl_label = "AI Toolkit Launcher Click"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        # Disable cheaply when the user has hidden the button.
        ui = _aitk_ui(context)
        if ui is None:
            return False
        if not bool(getattr(ui, "show_launcher_button", True)):
            return False
        area = context.area
        return area is not None and area.type == "VIEW_3D"

    def invoke(self, context, event):
        ui = _aitk_ui(context)
        if ui is None:
            return {"PASS_THROUGH"}
        x, y, w, h = get_button_rect(ui)
        mx = int(getattr(event, "mouse_region_x", -1))
        my = int(getattr(event, "mouse_region_y", -1))
        if x <= mx <= x + w and y <= my <= y + h:
            try:
                bpy.ops.aitk.quick_launcher("INVOKE_DEFAULT")
            except Exception:
                logger.exception(
                    "Launcher click router: aitk.quick_launcher failed"
                )
                return {"CANCELLED"}
            return {"FINISHED"}
        return {"PASS_THROUGH"}


# ----------------------------------------------------------------------
# Register / unregister
# ----------------------------------------------------------------------


def register_overlay() -> None:
    """Add the draw handler, register the click router, bind the keymap."""
    global _draw_handle, _draw_error_count

    # 1) Click router operator.
    try:
        bpy.utils.register_class(AITK_OT_launcher_click_router)
    except ValueError:
        bpy.utils.unregister_class(AITK_OT_launcher_click_router)
        bpy.utils.register_class(AITK_OT_launcher_click_router)

    # 2) Draw handler — idempotent guard, no double-add on hot reload.
    if _draw_handle is None:
        try:
            _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                _draw_callback, (), "WINDOW", "POST_PIXEL"
            )
            _draw_error_count = 0
        except Exception as exc:  # noqa: BLE001 — never abort register
            logger.warning(
                "Could not add launcher overlay draw handler (%s: %s); "
                "use the 3D Viewport header icon or press N to open the "
                "sidebar instead",
                type(exc).__name__,
                exc,
            )
            _draw_handle = None

    # 3) Keymap entry — bind LEFTMOUSE PRESS in the 3D View keymap so
    #    the click router runs before object selection. The keymap is
    #    looked up on the *addon* keyconfig so unregistering removes
    #    only our binding.
    wm = bpy.context.window_manager
    kc = getattr(getattr(wm, "keyconfigs", None), "addon", None) if wm is not None else None
    if kc is not None:
        try:
            km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
            kmi = km.keymap_items.new(
                AITK_OT_launcher_click_router.bl_idname,
                type="LEFTMOUSE",
                value="PRESS",
            )
            _keymap_entries.append((km, kmi))
        except Exception as exc:  # noqa: BLE001 — never abort register
            logger.warning(
                "Could not bind launcher click router to LEFTMOUSE "
                "(%s: %s); the floating launcher will draw but not "
                "respond to clicks. Use the header icon as fallback.",
                type(exc).__name__,
                exc,
            )


def unregister_overlay() -> None:
    """Remove the draw handler, unbind the keymap, unregister the router."""
    global _draw_handle, _draw_error_count

    # 1) Keymap entries first so an in-flight click doesn't hit a
    #    half-torn-down draw handler.
    for km, kmi in list(_keymap_entries):
        try:
            km.keymap_items.remove(kmi)
        except Exception:  # noqa: BLE001
            pass
    _keymap_entries.clear()

    # 2) Draw handler.
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        except Exception:  # noqa: BLE001 — may already be removed
            pass
        _draw_handle = None
        _draw_error_count = 0

    # 3) Operator.
    try:
        bpy.utils.unregister_class(AITK_OT_launcher_click_router)
    except Exception:  # noqa: BLE001 — may already be gone
        pass


__all__ = [
    "AITK_OT_launcher_click_router",
    "register_overlay",
    "unregister_overlay",
    "get_button_rect",
    "BUTTON_SIZE",
]
