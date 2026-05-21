"""AI Toolkit Platform — Blender-native sidebar UI.

This package is the public UI surface of the addon. It deliberately uses
only the standard ``bpy.types.Panel`` / ``bpy.types.Operator`` widgets so
the result looks and feels like a first-party Blender tool: collapsible
sub-panels in the N-panel sidebar, native icons, native hover, no
floating overlays, no custom GPU drawing.

Public entry points:

* :func:`register` — registers every property group, operator, and panel
  used by the new UI and attaches the ``Scene.aitk`` pointer-property
  used as the addon's per-scene state.
* :func:`unregister` — reverse of :func:`register`. Both are idempotent
  and safe to call during a hot reload.
"""

from __future__ import annotations

import bpy

from . import preferences, presets, ops, panels


# Order matters: PropertyGroups must be registered before they are used
# as PointerProperty targets, and Operators must be registered before
# Panels that reference them.
_classes: tuple = (
    # PropertyGroups — registered first so they are valid targets for the
    # PointerProperty attached to Scene below.
    preferences.AITK_PG_text_to_3d,
    preferences.AITK_PG_image_to_3d,
    preferences.AITK_PG_texturing,
    preferences.AITK_PG_render_preview,
    preferences.AITK_PG_assistant,
    preferences.AITK_PG_ui_state,
    preferences.AITK_PG_root,
    # Operators — stubs that drive button feedback for the visual pass.
    ops.AITK_OT_generate,
    ops.AITK_OT_cancel,
    ops.AITK_OT_pick_object,
    ops.AITK_OT_toggle_compact,
    ops.AITK_OT_set_accent,
    ops.AITK_OT_open_docs,
    ops.AITK_OT_open_preferences,
    ops.AITK_OT_assistant_send,
    ops.AITK_OT_assistant_quick,
    # Panels — root first, then sub-panels in display order.
    panels.AITK_PT_root,
    panels.AITK_PT_text_to_3d,
    panels.AITK_PT_image_to_3d,
    panels.AITK_PT_texturing,
    panels.AITK_PT_render_preview,
    panels.AITK_PT_assistant,
    panels.AITK_PT_settings,
)


def register() -> None:
    """Register every class and attach the per-scene state pointer."""
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # Already registered — happens on hot reload. Unregister and
            # retry so the new class object is used.
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)

    # One pointer-property on the Scene holds the entire UI state tree.
    # This survives .blend save/load so the user's prompt, last preset,
    # accent colour, etc. persist with the file.
    bpy.types.Scene.aitk = bpy.props.PointerProperty(type=preferences.AITK_PG_root)


def unregister() -> None:
    """Unregister every class and detach the per-scene state pointer."""
    # Detach the Scene pointer first so panels that read it during an
    # unregister-triggered redraw see ``None`` rather than a dangling
    # PropertyGroup.
    if hasattr(bpy.types.Scene, "aitk"):
        try:
            del bpy.types.Scene.aitk
        except Exception:  # noqa: BLE001 — never abort unregister
            pass

    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001 — class may already be gone
            pass


__all__ = ["register", "unregister"]
