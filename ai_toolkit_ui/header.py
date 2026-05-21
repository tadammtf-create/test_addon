"""3D-Viewport header entry point.

Adds a small icon button to ``bpy.types.VIEW3D_HT_header`` that opens
the AI Toolkit sidebar in one click. This is the discoverability hook
that replaces the old floating launcher overlay: it lives in a place
Blender users already scan (the viewport's top header bar — the same
place BlenderKit and several other production-grade addons add their
entry points), so the addon has an obvious "I am here" surface without
ever painting over the viewport itself.

The button:
* Shows the AI Toolkit accent icon (``SHADERFX``).
* On click, opens the right-side N-panel and switches the active
  category to "AI Toolkit" so the user lands directly on the panel.
"""

from __future__ import annotations

import logging

import bpy


logger = logging.getLogger("ai_toolkit")


class AITK_OT_open_sidebar(bpy.types.Operator):
    """Open the 3D Viewport N-panel and switch to the AI Toolkit tab.

    Best-effort: the sidebar is opened via the documented
    ``space.show_region_ui`` flag, and the active category is set on
    every visible 3D Viewport so the user sees the AI Toolkit panel
    immediately. Failures (older Blender that doesn't expose
    ``active_panel_category``, or a context without ``space_data``) are
    logged at debug level and the operator still returns ``FINISHED``
    so the button never looks broken.
    """

    bl_idname = "aitk.open_sidebar"
    bl_label = "AI Toolkit"
    bl_description = "Open the AI Toolkit sidebar"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        screen = getattr(context, "screen", None)
        if screen is None:
            return {"FINISHED"}

        for area in getattr(screen, "areas", ()) or ():
            if getattr(area, "type", None) != "VIEW_3D":
                continue
            # Force the UI region open on every 3D Viewport. The space
            # data of the area's first space holds the toggle.
            space = getattr(area, "spaces", None)
            if space is not None and len(space) > 0:
                try:
                    space[0].show_region_ui = True
                except Exception:  # noqa: BLE001 — defensive across versions
                    logger.debug(
                        "Could not toggle show_region_ui on a 3D Viewport",
                        exc_info=True,
                    )

            # Switch the sidebar's active category to "AI Toolkit". The
            # attribute name varies slightly between Blender versions,
            # so we try the documented path first and fall back to a
            # tag_redraw for older builds.
            for region in getattr(area, "regions", ()) or ():
                if getattr(region, "type", None) != "UI":
                    continue
                try:
                    region.active_panel_category = "AI Toolkit"
                except Exception:  # noqa: BLE001 — varies per Blender version
                    logger.debug(
                        "active_panel_category unavailable on this Blender",
                        exc_info=True,
                    )
                region.tag_redraw()
            area.tag_redraw()

        return {"FINISHED"}


def _draw_header_button(self, context):
    """Append the AI Toolkit icon button to the 3D Viewport header.

    Bound to :class:`bpy.types.VIEW3D_HT_header` via
    :func:`register_header`. Renders a small icon-only operator
    button so it slots into the existing header strip without taking
    visual space away from Blender's own controls.
    """
    layout = self.layout
    layout.separator(factor=0.4)
    layout.operator(
        "aitk.open_sidebar",
        text="",
        icon="SHADERFX",
        emboss=False,
    )


def register_header():
    """Register the operator and append the header draw function.

    Idempotent: ``append`` is a no-op if the same function reference
    is already in the draw list, but we still try/except the
    registration so a Blender version that renamed
    :class:`VIEW3D_HT_header` does not abort the whole addon.
    """
    try:
        bpy.utils.register_class(AITK_OT_open_sidebar)
    except ValueError:
        bpy.utils.unregister_class(AITK_OT_open_sidebar)
        bpy.utils.register_class(AITK_OT_open_sidebar)

    try:
        bpy.types.VIEW3D_HT_header.append(_draw_header_button)
    except Exception as exc:  # noqa: BLE001 — never abort register
        logger.warning(
            "Could not append AI Toolkit button to 3D Viewport header "
            "(%s: %s); use the sidebar (N) > AI Toolkit tab instead",
            type(exc).__name__,
            exc,
        )


def unregister_header():
    """Remove the header draw function and unregister the operator."""
    try:
        bpy.types.VIEW3D_HT_header.remove(_draw_header_button)
    except Exception:  # noqa: BLE001 — function may already be gone
        pass

    try:
        bpy.utils.unregister_class(AITK_OT_open_sidebar)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "AITK_OT_open_sidebar",
    "register_header",
    "unregister_header",
]
