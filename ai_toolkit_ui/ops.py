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

import logging

import bpy

from . import presets


logger = logging.getLogger("ai_toolkit")


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

# Icon used in the Quick Launcher's module grid for each module.
_MODULE_ICON = {
    "TEXT_TO_3D": "FONT_DATA",
    "IMAGE_TO_3D": "IMAGE_DATA",
    "TEXTURING": "NODE_TEXTURE",
    "RENDER_PREVIEW": "RESTRICT_RENDER_OFF",
    "ASSISTANT": "OUTLINER_DATA_LIGHTPROBE",
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
        area.tag_redraw()


def _push_recent(root, *, prompt: str, module: str) -> None:
    """Push a new recent-actions entry to the top of the list.

    Keeps the most recent 6 entries — beyond that the oldest are
    dropped. Trims the prompt to 60 characters so the popup never
    has to render a runaway label.
    """
    if not prompt:
        return
    item = root.recent.add()
    item.text = prompt[:60]
    item.module = _MODULE_LABELS.get(module, module)
    item.label = "just now"
    # The freshly-added item is at the end of the collection — move it
    # to index 0 so the popup's first row is always the most recent.
    try:
        root.recent.move(len(root.recent) - 1, 0)
    except Exception:  # noqa: BLE001 — CollectionProperty.move may be picky
        pass
    while len(root.recent) > 6:
        try:
            root.recent.remove(len(root.recent) - 1)
        except Exception:  # noqa: BLE001
            break


def _global_status(root):
    """Resolve a single (label, icon, alert) tuple from every module's state."""
    states = []
    for attr in _MODULE_ATTR.values():
        module = getattr(root, attr, None)
        if module is not None:
            states.append(getattr(module, "status", "Ready") or "Ready")

    if any(s == "Running…" for s in states):
        return ("Running", "SORTTIME", False)
    if any(s.startswith(("Failed", "Error")) for s in states):
        return ("Error", "ERROR", True)
    return ("Ready", "CHECKMARK", False)


# ----------------------------------------------------------------------
# Generic generate / cancel
# ----------------------------------------------------------------------


class AITK_OT_generate(bpy.types.Operator):
    """Stub generate action — updates the matching module's status row.

    Also pushes the current prompt onto the recent-actions list so the
    Quick Launcher's "Recent" section feels alive. The actual
    generation pipeline is intentionally out of scope for the visual
    pass.
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

        # Push the prompt to the recent list so the Quick Launcher
        # surfaces it. Each module exposes a slightly different prompt
        # field; we resolve them in turn and fall back to a placeholder.
        prompt = (
            getattr(module, "prompt", None)
            or getattr(module, "image_path", None)
            or getattr(module, "preset", None)
            or ""
        )
        _push_recent(root, prompt=str(prompt), module=self.module)

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
        _push_recent(root, prompt=prompt, module="ASSISTANT")
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


# ----------------------------------------------------------------------
# Quick Launcher popup — the click target of the floating button
# ----------------------------------------------------------------------


class AITK_OT_quick_launcher(bpy.types.Operator):
    """Open the compact AI Toolkit Quick Launcher popup.

    Uses :meth:`bpy.types.WindowManager.invoke_popup` so Blender owns
    the popup window, including auto-positioning inside the screen and
    click-outside-to-close behaviour. The popup hosts a quick prompt
    input, a primary Generate action, a 5-module shortcut grid, a
    recent-actions list and footer shortcuts. Full settings stay in
    the N-panel.
    """

    bl_idname = "aitk.quick_launcher"
    bl_label = "AI Toolkit"
    bl_description = "Open the AI Toolkit Quick Launcher"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        # 300 px is wide enough for a two-row module grid and the
        # quick-prompt input on a 1× UI scale and still narrow enough
        # to feel like a floating launcher rather than a panel.
        return context.window_manager.invoke_popup(self, width=300)

    def execute(self, context):
        # ``invoke_popup`` closes the popup when the user clicks
        # outside; ``execute`` is only reached if something dispatches
        # to this operator without ``invoke``. Nothing to do.
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="AI Toolkit not initialised", icon="ERROR")
            return

        ui = root.ui

        # ── Header ────────────────────────────────────────────────────
        header = layout.row(align=True)
        header.label(text="AI Toolkit", icon="SHADERFX")
        sub = header.row(align=True)
        sub.alignment = "RIGHT"
        sub.enabled = False
        sub.label(text="Quick Launcher")

        # ── Status row ────────────────────────────────────────────────
        status_label, status_icon, alert = _global_status(root)
        status = layout.row(align=True)
        status.scale_y = 0.85
        status.alert = alert
        status.label(text=status_label, icon=status_icon)
        meta = status.row(align=True)
        meta.alignment = "RIGHT"
        meta.enabled = False
        meta.label(text=f"Accent · {ui.accent_color.title()}")

        layout.separator(factor=0.4)

        # ── Quick prompt + Generate ───────────────────────────────────
        box = layout.box()
        box.scale_y = 0.95
        head = box.row(align=True)
        head.label(text="Quick Prompt", icon="GREASEPENCIL")
        target = head.row(align=True)
        target.alignment = "RIGHT"
        target.enabled = False
        target.label(text="→ Text-to-3D")

        box.prop(root.text_to_3d, "prompt", text="")

        gen_row = box.row(align=True)
        gen_row.scale_y = 1.35
        op = gen_row.operator("aitk.generate", text="Generate", icon="PLAY")
        op.module = "TEXT_TO_3D"

        # ── Module shortcut grid ──────────────────────────────────────
        layout.separator(factor=0.4)
        head = layout.row(align=True)
        head.label(text="Modules", icon="OUTLINER")

        grid = layout.grid_flow(
            row_major=True, columns=3, even_columns=True, align=True,
        )
        grid.scale_y = 1.2
        for key, label in _MODULE_LABELS.items():
            icon = _MODULE_ICON.get(key, "DOT")
            op = grid.operator(
                "aitk.open_module",
                text=label.replace("AI ", "").replace("Render Preview", "Render"),
                icon=icon,
            )
            op.module = key

        # ── Recent actions ────────────────────────────────────────────
        layout.separator(factor=0.4)
        head = layout.row(align=True)
        head.label(text="Recent", icon="TIME")
        clear = head.row(align=True)
        clear.alignment = "RIGHT"
        clear.scale_x = 0.9
        if len(root.recent) > 0:
            clear.operator("aitk.clear_recent", text="", icon="X", emboss=False)

        recent_box = layout.box()
        recent_box.scale_y = 0.85
        if len(root.recent) == 0:
            empty = recent_box.row()
            empty.enabled = False
            empty.label(text="No recent actions yet", icon="DOT")
        else:
            for item in list(root.recent)[:4]:
                row = recent_box.row(align=True)
                row.alignment = "LEFT"
                text = item.text or "(no prompt)"
                if len(text) > 32:
                    text = text[:31] + "…"
                row.label(text=text, icon="DOT")
                meta = row.row(align=True)
                meta.alignment = "RIGHT"
                meta.enabled = False
                meta.label(text=item.module)

        # ── Footer shortcuts ──────────────────────────────────────────
        layout.separator(factor=0.4)
        footer = layout.row(align=True)
        footer.scale_y = 1.0
        footer.operator(
            "aitk.open_sidebar", text="Open Panel", icon="MENU_PANEL",
        )
        footer.operator(
            "aitk.open_docs", text="Docs", icon="HELP",
        )
        footer.operator(
            "aitk.open_preferences", text="Prefs", icon="PREFERENCES",
        )


# ----------------------------------------------------------------------
# Module shortcut — used by Quick Launcher grid
# ----------------------------------------------------------------------


class AITK_OT_open_module(bpy.types.Operator):
    """Open the N-panel and focus a specific module.

    Used by the Quick Launcher's module grid. Opens the AI Toolkit
    sidebar tab (via ``aitk.open_sidebar``) and stamps the chosen
    module key on ``ui.active_module`` so a follow-up enhancement can
    auto-expand the matching sub-panel.
    """

    bl_idname = "aitk.open_module"
    bl_label = "Open Module"
    bl_description = "Open this module's full panel in the sidebar"
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
            return {"CANCELLED"}
        # Record the active module so the sidebar can react visually.
        try:
            root.ui.active_module = self.module
        except (TypeError, AttributeError):
            pass

        # Best-effort — the click router calls this from popup context.
        try:
            bpy.ops.aitk.open_sidebar()
        except Exception:
            logger.debug("aitk.open_sidebar dispatch failed", exc_info=True)

        label = _MODULE_LABELS.get(self.module, self.module)
        self.report({"INFO"}, f"Open: {label}")
        _tag_redraw(context)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Clear recent
# ----------------------------------------------------------------------


class AITK_OT_clear_recent(bpy.types.Operator):
    """Clear the Quick Launcher's recent-actions list."""

    bl_idname = "aitk.clear_recent"
    bl_label = "Clear Recent"
    bl_description = "Remove all entries from the recent-actions list"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        root = _aitk(context)
        if root is None:
            return {"CANCELLED"}
        root.recent.clear()
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
    "AITK_OT_quick_launcher",
    "AITK_OT_open_module",
    "AITK_OT_clear_recent",
]
