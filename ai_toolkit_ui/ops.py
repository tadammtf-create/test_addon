"""Stub operators used by the AI Toolkit sidebar.

Every operator here is a UI placeholder: it reports a status, updates
the relevant module's ``status`` string, and returns. The real
generation pipelines wire into these operator ids in a follow-up; this
module exists so every button in the panel is wired to a registered
operator and Blender renders it as an enabled, clickable widget rather
than a disabled "missing operator" warning.

Operator naming uses the ``aitk`` category prefix.
"""

from __future__ import annotations

import bpy


# Map module -> human label, used by the generic generate / cancel ops
# so they can report which module they targeted.
_MODULE_LABELS = {
    "TEXT_TO_3D": "Text-to-3D",
    "IMAGE_TO_3D": "Image-to-3D",
    "TEXTURING": "AI Texturing",
    "RENDER_PREVIEW": "Render Preview",
    "ASSISTANT": "AI Assistant",
}

# Maps the module key to the PropertyGroup attribute name on
# ``scene.aitk`` so a single Generate operator can update the right
# status field.
_MODULE_ATTR = {
    "TEXT_TO_3D": "text_to_3d",
    "IMAGE_TO_3D": "image_to_3d",
    "TEXTURING": "texturing",
    "RENDER_PREVIEW": "render_preview",
    "ASSISTANT": "assistant",
}


def _aitk(context):
    """Return the root state PointerProperty or ``None`` if not registered."""
    scene = getattr(context, "scene", None)
    return getattr(scene, "aitk", None) if scene is not None else None


def _tag_redraw(context) -> None:
    """Force every 3D Viewport sidebar region to redraw immediately."""
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in getattr(screen, "areas", ()) or ():
        if getattr(area, "type", None) != "VIEW_3D":
            continue
        for region in getattr(area, "regions", ()) or ():
            if getattr(region, "type", None) == "UI":
                region.tag_redraw()


# ----------------------------------------------------------------------
# Generic generate / cancel
# ----------------------------------------------------------------------


class AITK_OT_generate(bpy.types.Operator):
    """Stub generate action — updates the matching module's status row.

    The actual generation pipeline is intentionally out of scope for the
    visual pass. The operator records a deterministic status string so
    the panel layout exercises every status branch (idle / running /
    succeeded / failed) by changing this property elsewhere.
    """

    bl_idname = "aitk.generate"
    bl_label = "Generate"
    bl_description = "Run the selected module's generator (stub)"
    bl_options = {"INTERNAL"}

    module: bpy.props.EnumProperty(
        name="Module",
        items=tuple(
            (key, label, label) for key, label in _MODULE_LABELS.items()
        ),
        default="TEXT_TO_3D",
        options={"HIDDEN"},
    )

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            self.report({"ERROR"}, "AI Toolkit not initialised")
            return {"CANCELLED"}

        attr = _MODULE_ATTR.get(self.module)
        module = getattr(root, attr, None) if attr else None
        label = _MODULE_LABELS.get(self.module, "Module")

        if module is None:
            self.report({"ERROR"}, f"{label} state missing")
            return {"CANCELLED"}

        module.status = "Running…"
        self.report({"INFO"}, f"{label}: queued (stub)")
        _tag_redraw(context)
        return {"FINISHED"}


class AITK_OT_cancel(bpy.types.Operator):
    """Stub cancel action — resets the matching module's status row."""

    bl_idname = "aitk.cancel"
    bl_label = "Cancel"
    bl_description = "Cancel the current job for this module"
    bl_options = {"INTERNAL"}

    module: bpy.props.EnumProperty(
        name="Module",
        items=tuple(
            (key, label, label) for key, label in _MODULE_LABELS.items()
        ),
        default="TEXT_TO_3D",
        options={"HIDDEN"},
    )

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            self.report({"ERROR"}, "AI Toolkit not initialised")
            return {"CANCELLED"}

        attr = _MODULE_ATTR.get(self.module)
        module = getattr(root, attr, None) if attr else None
        label = _MODULE_LABELS.get(self.module, "Module")

        if module is None:
            self.report({"ERROR"}, f"{label} state missing")
            return {"CANCELLED"}

        module.status = "Ready"
        self.report({"INFO"}, f"{label}: cancelled")
        _tag_redraw(context)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Texturing — pick active as target shortcut
# ----------------------------------------------------------------------


class AITK_OT_pick_object(bpy.types.Operator):
    """Set the AI Texturing target to the active 3D Viewport object."""

    bl_idname = "aitk.pick_active_object"
    bl_label = "Use Active"
    bl_description = "Use the active object as the AI Texturing target"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "active_object", None) is not None

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            self.report({"ERROR"}, "AI Toolkit not initialised")
            return {"CANCELLED"}
        root.texturing.target = context.active_object
        self.report({"INFO"}, f"Target: {context.active_object.name}")
        _tag_redraw(context)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Compact toggle
# ----------------------------------------------------------------------


class AITK_OT_toggle_compact(bpy.types.Operator):
    """Toggle the panel's compact (single-column dense) layout."""

    bl_idname = "aitk.toggle_compact"
    bl_label = "Compact Mode"
    bl_description = "Toggle compact panel layout for narrow sidebars"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            return {"CANCELLED"}
        root.ui.compact_mode = not root.ui.compact_mode
        _tag_redraw(context)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Set accent (clicked from a small accent swatch row)
# ----------------------------------------------------------------------


class AITK_OT_set_accent(bpy.types.Operator):
    """Set the addon's accent colour from a swatch click."""

    bl_idname = "aitk.set_accent"
    bl_label = "Set Accent"
    bl_description = "Pick this colour as the addon accent"
    bl_options = {"INTERNAL"}

    accent: bpy.props.StringProperty(
        name="Accent",
        default="BLUE",
        options={"HIDDEN"},
    )

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            return {"CANCELLED"}
        try:
            root.ui.accent_color = self.accent
        except TypeError:
            # Unknown enum value — fall back to BLUE so the operator
            # never leaves the UI in a half-set state.
            root.ui.accent_color = "BLUE"
        _tag_redraw(context)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# About / Docs / Preferences shortcuts
# ----------------------------------------------------------------------


class AITK_OT_open_docs(bpy.types.Operator):
    """Open the AI Toolkit documentation in the default browser."""

    bl_idname = "aitk.open_docs"
    bl_label = "Documentation"
    bl_description = "Open the AI Toolkit documentation"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            bpy.ops.wm.url_open(url="https://example.com/ai-toolkit-docs")
        except Exception:
            self.report({"INFO"}, "Documentation: https://example.com/ai-toolkit-docs")
        return {"FINISHED"}


class AITK_OT_open_preferences(bpy.types.Operator):
    """Open Blender's preferences window scoped to addons."""

    bl_idname = "aitk.open_preferences"
    bl_label = "Preferences"
    bl_description = "Open the addon preferences"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
        except Exception:
            self.report({"INFO"}, "Open Edit > Preferences > Add-ons")
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Assistant — send and quick action
# ----------------------------------------------------------------------


class AITK_OT_assistant_send(bpy.types.Operator):
    """Send the assistant prompt — updates the mock response field."""

    bl_idname = "aitk.assistant_send"
    bl_label = "Send"
    bl_description = "Send the question to the AI Assistant"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            return {"CANCELLED"}
        assistant = root.assistant
        prompt = (assistant.prompt or "").strip()
        if not prompt:
            self.report({"WARNING"}, "Type a question first")
            return {"CANCELLED"}
        assistant.status = "Thinking…"
        assistant.last_response = (
            f"Stub response to: {prompt[:120]}"
            + ("…" if len(prompt) > 120 else "")
        )
        self.report({"INFO"}, "Assistant: response ready (stub)")
        _tag_redraw(context)
        return {"FINISHED"}


class AITK_OT_assistant_quick(bpy.types.Operator):
    """Trigger an Assistant quick-action."""

    bl_idname = "aitk.assistant_quick"
    bl_label = "Quick Action"
    bl_description = "Run a predefined assistant quick-action"
    bl_options = {"INTERNAL"}

    action: bpy.props.StringProperty(
        name="Action",
        default="describe",
        options={"HIDDEN"},
    )

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            return {"CANCELLED"}
        assistant = root.assistant
        label = self.action.replace("_", " ").title()
        assistant.last_response = f"Stub: would run '{label}' on the active scene."
        assistant.status = "Idle"
        self.report({"INFO"}, f"Assistant: {label} (stub)")
        _tag_redraw(context)
        return {"FINISHED"}


__all__ = [
    "AITK_OT_generate",
    "AITK_OT_cancel",
    "AITK_OT_pick_object",
    "AITK_OT_toggle_compact",
    "AITK_OT_set_accent",
    "AITK_OT_open_docs",
    "AITK_OT_open_preferences",
    "AITK_OT_assistant_send",
    "AITK_OT_assistant_quick",
]
