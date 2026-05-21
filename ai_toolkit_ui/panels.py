"""Sidebar panels — the entire AI Toolkit visual surface lives here.

Every panel is a plain :class:`bpy.types.Panel` rendered in the 3D
Viewport's ``UI`` region under a single ``AI Toolkit`` tab. The root
panel hosts the brand row, theme / accent / compact toggles and a small
quick-action strip. Each module is a separate sub-panel parented to the
root so the user can collapse the sections they aren't using.

Design rules:

* No custom GPU drawing. Only native Blender widgets.
* Compact-mode-aware: every module respects
  ``scene.aitk.ui.compact_mode`` by hiding secondary labels, tips and
  resolved-status rows so the panel fits a narrow N-panel.
* No floating overlays or modal menus. The launcher overlay from the
  previous design is intentionally not registered.
"""

from __future__ import annotations

import bpy

from . import presets


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# Single category — all panels live under the same sidebar tab so the
# user toggles modules with the sub-panel collapse arrow instead of
# hopping between tabs.
_TAB = "AI Toolkit"

# Big primary-action buttons are rendered taller than default so they
# read as the "click me" target in each module. 1.4 is the value
# Blender's own File Browser uses for the Open / Save buttons.
_PRIMARY_SCALE = 1.4


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _aitk(context):
    """Return the root state PropertyGroup or ``None`` when un-registered."""
    scene = getattr(context, "scene", None)
    return getattr(scene, "aitk", None) if scene is not None else None


def _is_compact(root) -> bool:
    """``True`` when the user has enabled the panel's compact layout."""
    return bool(root is not None and getattr(root.ui, "compact_mode", False))


def _section_header(layout, text, icon="DOT"):
    """Render a small uppercase-style section divider.

    Blender doesn't expose first-class section headers, so we use a thin
    row with a muted icon + label and an inline separator beneath it.
    """
    row = layout.row(align=True)
    row.scale_y = 0.7
    row.label(text=text, icon=icon)


def _status_row(layout, status: str, *, compact: bool) -> None:
    """Render a status line — hidden in compact mode unless non-idle.

    The status row is the smallest UI element in each module. In
    compact mode we only render it when the status is not the default
    ``Ready`` / ``Idle`` so empty panels stay quiet.
    """
    if not status:
        return
    quiet = status in ("Ready", "Idle")
    if compact and quiet:
        return
    row = layout.row()
    row.scale_y = 0.7
    icon = "DOT"
    if status == "Running…":
        icon = "SORTTIME"
    elif status.startswith("Failed") or status.startswith("Error"):
        icon = "ERROR"
        row.alert = True
    elif status == "Done" or status == "Succeeded":
        icon = "CHECKMARK"
    row.label(text=status, icon=icon)


def _generate_row(layout, *, module: str, label: str = "Generate") -> None:
    """Render the prominent primary-action Generate button.

    ``module`` is the :class:`bpy.props.EnumProperty` value passed to
    :class:`~ai_toolkit_ui.ops.AITK_OT_generate` so a single operator
    serves every module.
    """
    row = layout.row(align=True)
    row.scale_y = _PRIMARY_SCALE
    op = row.operator("aitk.generate", text=label, icon="PLAY")
    op.module = module


def _cancel_row(layout, *, module: str) -> None:
    """Render the small Cancel button below Generate."""
    row = layout.row(align=True)
    row.scale_y = 0.9
    op = row.operator("aitk.cancel", text="Cancel", icon="X")
    op.module = module


def _labelled_prop(
    layout,
    data,
    prop_name: str,
    text: str,
    *,
    compact: bool,
    icon: str = "NONE",
) -> None:
    """Render a property with an inline label that respects compact mode.

    In normal mode: label-then-control on two rows for breathing room
    on narrow sidebars (split layouts squeeze the input).
    In compact mode: control only, using its own placeholder text.
    """
    if compact:
        layout.prop(data, prop_name, text="")
        return
    col = layout.column(align=True)
    header = col.row(align=True)
    header.scale_y = 0.85
    header.label(text=text, icon=icon)
    col.prop(data, prop_name, text="")


# ----------------------------------------------------------------------
# Root panel
# ----------------------------------------------------------------------


class AITK_PT_root(bpy.types.Panel):
    """Brand header + global controls. Hosts every module sub-panel.

    Always visible at the top of the AI Toolkit sidebar tab so the user
    can reach accent / compact / docs without scrolling, and so a
    collapsed module panel still has somewhere to expand back from.
    """

    bl_idname = "AITK_PT_root"
    bl_label = "AI Toolkit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB

    def draw_header(self, context):
        """Replace the default chevron-with-text header with an icon-led brand."""
        layout = self.layout
        row = layout.row(align=True)
        row.label(text="", icon="SHADERFX")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        compact = _is_compact(root)

        # ── Brand row ──────────────────────────────────────────────────
        # Two-line brand block so the tagline can be hidden in compact
        # mode without sacrificing the wordmark.
        brand = layout.column(align=True)
        title = brand.row(align=True)
        title.scale_y = 1.0
        title.label(text="AI Toolkit", icon="SHADERFX")
        if not compact:
            tagline = brand.row(align=True)
            tagline.scale_y = 0.75
            tagline.enabled = False
            tagline.label(text="Production AI tools for Blender")

        layout.separator(factor=0.4)

        # ── Accent swatch strip ────────────────────────────────────────
        # Six icon-only operator buttons, one per accent. Operators
        # depress visually when their value matches the current
        # selection so the strip doubles as state indicator + picker.
        if root is not None:
            if not compact:
                _section_header(layout, "Accent", icon="COLOR")
            swatch = layout.row(align=True)
            swatch.scale_y = 0.95
            for key, label, _desc, icon in presets.ACCENT_COLORS:
                sub = swatch.row(align=True)
                sub.scale_x = 1.0
                # The depress kwarg gives native pressed-state styling
                # so the active accent reads as selected without us
                # drawing anything custom.
                op = sub.operator(
                    "aitk.set_accent",
                    text="",
                    icon=icon,
                    depress=(root.ui.accent_color == key),
                )
                op.accent = key

        # ── Layout / theme toolbar ─────────────────────────────────────
        layout.separator(factor=0.4)
        tools = layout.row(align=True)
        if root is not None:
            tools.prop(
                root.ui,
                "compact_mode",
                text="Compact" if not compact else "",
                icon="ALIGN_JUSTIFY",
                toggle=True,
            )
            tools.prop(
                root.ui,
                "show_tips",
                text="Tips" if not compact else "",
                icon="QUESTION",
                toggle=True,
            )
            tools.prop(
                root.ui,
                "theme_mode",
                text="",
                icon_only=True,
            )

        # Docs + preferences mini row — keeps shortcut surface area
        # small but lets the user reach the boring controls fast.
        if not compact:
            layout.separator(factor=0.3)
            shortcuts = layout.row(align=True)
            shortcuts.scale_y = 0.9
            shortcuts.operator(
                "aitk.open_docs",
                text="Docs",
                icon="HELP",
            )
            shortcuts.operator(
                "aitk.open_preferences",
                text="Preferences",
                icon="PREFERENCES",
            )

        # Module section header — also tells the user the next panels
        # below are children of this root.
        if not compact:
            layout.separator(factor=0.6)
            _section_header(layout, "Modules", icon="OUTLINER")


# ----------------------------------------------------------------------
# Sub-panel base: shared module helpers
# ----------------------------------------------------------------------


def _module_header(panel, context, *, title: str, icon: str) -> None:
    """Render the small module header in :meth:`draw_header`.

    Used by every module so the icon position and spacing match across
    modules. Blender renders this inline with its built-in collapse
    chevron, so the user gets free hover highlighting on the whole row.
    """
    panel.layout.label(text="", icon=icon)


# ----------------------------------------------------------------------
# Text-to-3D
# ----------------------------------------------------------------------


class AITK_PT_text_to_3d(bpy.types.Panel):
    """Text-to-3D module sub-panel."""

    bl_idname = "AITK_PT_text_to_3d"
    bl_label = "Text-to-3D"
    bl_parent_id = "AITK_PT_root"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB
    bl_options = set()  # open by default — this is the most common entry

    def draw_header(self, context):
        _module_header(self, context, title="Text-to-3D", icon="FONT_DATA")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="Not registered", icon="ERROR")
            return
        compact = _is_compact(root)
        state = root.text_to_3d

        # Prompt block — the primary input for this module gets the
        # most visual weight. ``column(align=True)`` keeps the optional
        # label + the input glued together so they read as one unit.
        _labelled_prop(layout, state, "prompt", "Prompt", compact=compact, icon="GREASEPENCIL")

        if root.ui.show_tips and not compact:
            tip = layout.row(align=True)
            tip.scale_y = 0.7
            tip.enabled = False
            tip.label(text="Describe form, material and style", icon="INFO")

        # Preset + style row — side by side on wide enough sidebars.
        layout.separator(factor=0.3)
        grid = layout.column(align=True)
        grid.use_property_split = True
        grid.use_property_decorate = False
        grid.prop(state, "quality", text="Quality")
        grid.prop(state, "style", text="Style")

        # Advanced toggle — hidden in compact mode (it would be the
        # first thing users would want collapsed anyway).
        if not compact:
            layout.separator(factor=0.3)
            adv_row = layout.row(align=True)
            adv_icon = "DISCLOSURE_TRI_DOWN" if root.ui.show_advanced else "DISCLOSURE_TRI_RIGHT"
            adv_row.prop(
                root.ui,
                "show_advanced",
                text="Advanced",
                icon=adv_icon,
                emboss=False,
                toggle=True,
            )
            if root.ui.show_advanced:
                adv = layout.column(align=True)
                adv.use_property_split = True
                adv.use_property_decorate = False
                adv.prop(state, "polycount", text="Polycount")
                adv.prop(state, "seed", text="Seed")
                adv.prop(state, "enable_textures", text="Textures")

        # Primary action.
        layout.separator(factor=0.4)
        _generate_row(layout, module="TEXT_TO_3D")
        if state.status == "Running…":
            _cancel_row(layout, module="TEXT_TO_3D")

        _status_row(layout, state.status, compact=compact)


# ----------------------------------------------------------------------
# Image-to-3D
# ----------------------------------------------------------------------


class AITK_PT_image_to_3d(bpy.types.Panel):
    """Image-to-3D module sub-panel."""

    bl_idname = "AITK_PT_image_to_3d"
    bl_label = "Image-to-3D"
    bl_parent_id = "AITK_PT_root"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        _module_header(self, context, title="Image-to-3D", icon="IMAGE_DATA")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="Not registered", icon="ERROR")
            return
        compact = _is_compact(root)
        state = root.image_to_3d

        # File picker + thumbnail-info row.
        _labelled_prop(
            layout, state, "image_path", "Source Image",
            compact=compact, icon="IMAGE_REFERENCE",
        )

        if state.image_path:
            info = layout.row(align=True)
            info.scale_y = 0.7
            info.enabled = False
            # Show just the file name so the box doesn't blow wide.
            import os
            name = os.path.basename(state.image_path) or state.image_path
            info.label(text=name, icon="FILE_IMAGE")
        elif root.ui.show_tips and not compact:
            tip = layout.row(align=True)
            tip.scale_y = 0.7
            tip.enabled = False
            tip.label(text=".png · .jpg · .webp · ≤ 20 MB", icon="INFO")

        # Controls
        layout.separator(factor=0.3)
        controls = layout.column(align=True)
        controls.use_property_split = True
        controls.use_property_decorate = False
        controls.prop(state, "view_preset", text="View")
        controls.prop(state, "mesh_detail", text="Detail", slider=True)

        if not compact:
            layout.separator(factor=0.3)
            flags = layout.column(align=True)
            flags.use_property_split = False
            flags.prop(state, "remove_background", icon="MOD_MASK")
            flags.prop(state, "symmetry", icon="MOD_MIRROR")

        # Primary action
        layout.separator(factor=0.4)
        _generate_row(layout, module="IMAGE_TO_3D", label="Reconstruct")
        if state.status == "Running…":
            _cancel_row(layout, module="IMAGE_TO_3D")

        _status_row(layout, state.status, compact=compact)


# ----------------------------------------------------------------------
# AI Texturing
# ----------------------------------------------------------------------


class AITK_PT_texturing(bpy.types.Panel):
    """AI Texturing module sub-panel."""

    bl_idname = "AITK_PT_texturing"
    bl_label = "AI Texturing"
    bl_parent_id = "AITK_PT_root"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        _module_header(self, context, title="AI Texturing", icon="NODE_TEXTURE")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="Not registered", icon="ERROR")
            return
        compact = _is_compact(root)
        state = root.texturing

        # Target picker — Blender renders a native object eyedrop +
        # dropdown for PointerProperty(Object), which is exactly the
        # control we want for "pick the object to texture".
        target_col = layout.column(align=True)
        if not compact:
            head = target_col.row(align=True)
            head.scale_y = 0.85
            head.label(text="Target", icon="OBJECT_DATA")
        picker = target_col.row(align=True)
        picker.prop(state, "target", text="")
        picker.operator("aitk.pick_active_object", text="", icon="EYEDROPPER")

        # Prompt — the language the user wants the material to read as.
        layout.separator(factor=0.3)
        _labelled_prop(
            layout, state, "prompt", "Material Prompt",
            compact=compact, icon="GREASEPENCIL",
        )

        # Style + resolution
        layout.separator(factor=0.3)
        params = layout.column(align=True)
        params.use_property_split = True
        params.use_property_decorate = False
        params.prop(state, "style", text="Style")
        params.prop(state, "resolution", text="Resolution")

        # PBR map toggles — a single-row grid so the panel reads as
        # "pick the channels you need".
        if not compact:
            layout.separator(factor=0.3)
            _section_header(layout, "PBR Channels", icon="NODE_MATERIAL")

        maps = layout.grid_flow(
            row_major=True, columns=2 if not compact else 3,
            even_columns=True, align=True,
        )
        maps.prop(state, "use_albedo", toggle=True, icon="COLOR")
        maps.prop(state, "use_normal", toggle=True, icon="ORIENTATION_NORMAL")
        maps.prop(state, "use_roughness", toggle=True, icon="MATSHADERBALL")
        maps.prop(state, "use_metallic", toggle=True, icon="MATSPHERE")
        if not compact:
            maps.prop(state, "use_displacement", toggle=True, icon="MOD_DISPLACE")

        # Primary action
        layout.separator(factor=0.4)
        _generate_row(layout, module="TEXTURING", label="Texture")
        if state.status == "Running…":
            _cancel_row(layout, module="TEXTURING")

        _status_row(layout, state.status, compact=compact)


# ----------------------------------------------------------------------
# Render Preview
# ----------------------------------------------------------------------


class AITK_PT_render_preview(bpy.types.Panel):
    """Render Preview module sub-panel."""

    bl_idname = "AITK_PT_render_preview"
    bl_label = "Render Preview"
    bl_parent_id = "AITK_PT_root"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        _module_header(self, context, title="Render Preview", icon="RESTRICT_RENDER_OFF")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="Not registered", icon="ERROR")
            return
        compact = _is_compact(root)
        state = root.render_preview

        # Preset row gets the most attention — drives lighting + look.
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(state, "preset", text="Preset")
        col.prop(state, "samples", text="Samples")
        col.prop(state, "resolution_pct", text="Size")

        if not compact:
            layout.separator(factor=0.3)
            flags = layout.column(align=True)
            flags.prop(state, "denoise", icon="OUTLINER_OB_LIGHT")
            flags.prop(state, "transparent_bg", icon="IMAGE_RGB_ALPHA")

        # Primary action — render preview reads more naturally as
        # "Render" than "Generate", so override the button label.
        layout.separator(factor=0.4)
        _generate_row(layout, module="RENDER_PREVIEW", label="Render Preview")
        if state.status == "Running…":
            _cancel_row(layout, module="RENDER_PREVIEW")

        _status_row(layout, state.status, compact=compact)


# ----------------------------------------------------------------------
# AI Assistant
# ----------------------------------------------------------------------


class AITK_PT_assistant(bpy.types.Panel):
    """AI Assistant module sub-panel."""

    bl_idname = "AITK_PT_assistant"
    bl_label = "AI Assistant"
    bl_parent_id = "AITK_PT_root"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        _module_header(self, context, title="AI Assistant", icon="OUTLINER_DATA_LIGHTPROBE")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="Not registered", icon="ERROR")
            return
        compact = _is_compact(root)
        state = root.assistant

        # Mode picker — three pill-style buttons feel more chat-app
        # than a dropdown, which fits the conversational module.
        mode_row = layout.row(align=True)
        for key, label, _desc, icon in presets.ASSISTANT_MODES:
            sub = mode_row.row(align=True)
            sub.scale_y = 0.9
            sub.prop_enum(state, "mode", key, text=label if not compact else "", icon=icon)

        layout.separator(factor=0.3)

        # Input + send button on one line — chat-app pattern.
        chat = layout.row(align=True)
        chat.prop(state, "prompt", text="", icon="GREASEPENCIL")
        chat.operator("aitk.assistant_send", text="", icon="EXPORT")

        # Response readout — a muted multi-line label area. Blender
        # doesn't support multi-line labels natively, so we split on
        # whitespace into 36-character chunks.
        if not compact and state.last_response:
            layout.separator(factor=0.3)
            box = layout.box()
            box.scale_y = 0.85
            chunks = _wrap_text(state.last_response, width=36)
            for chunk in chunks[:6]:
                row = box.row()
                row.alignment = "LEFT"
                row.label(text=chunk)
            if len(chunks) > 6:
                more = box.row()
                more.scale_y = 0.75
                more.enabled = False
                more.label(text=f"+{len(chunks) - 6} more lines…")

        # Quick actions strip — opinionated shortcuts.
        if not compact:
            layout.separator(factor=0.3)
            _section_header(layout, "Quick Actions", icon="OUTLINER")
        quick = layout.grid_flow(
            row_major=True, columns=2, even_columns=True, align=True,
        )
        for action_id, action_label, action_icon in (
            ("describe_scene", "Describe", "VIEWZOOM"),
            ("suggest_lighting", "Light", "LIGHT"),
            ("optimise_mesh", "Optimise", "MOD_DECIM"),
            ("auto_rig", "Auto-Rig", "ARMATURE_DATA"),
        ):
            op = quick.operator(
                "aitk.assistant_quick",
                text=action_label if not compact else "",
                icon=action_icon,
            )
            op.action = action_id

        # Footer: toggle + status
        if not compact:
            layout.separator(factor=0.3)
            footer = layout.row(align=True)
            footer.prop(state, "auto_apply", toggle=True, icon="AUTO")

        _status_row(layout, state.status, compact=compact)


def _wrap_text(text: str, *, width: int = 40):
    """Simple greedy text wrapper used for the assistant transcript."""
    words = (text or "").split()
    lines, current, current_len = [], [], 0
    for word in words:
        wlen = len(word)
        if current_len == 0:
            current = [word]
            current_len = wlen
            continue
        if current_len + 1 + wlen > width:
            lines.append(" ".join(current))
            current = [word]
            current_len = wlen
        else:
            current.append(word)
            current_len += 1 + wlen
    if current:
        lines.append(" ".join(current))
    return lines


# ----------------------------------------------------------------------
# Settings — collapsed by default at the bottom
# ----------------------------------------------------------------------


class AITK_PT_settings(bpy.types.Panel):
    """Settings & About — accent picker, theme mode, docs / prefs."""

    bl_idname = "AITK_PT_settings"
    bl_label = "Settings"
    bl_parent_id = "AITK_PT_root"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _TAB
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="PREFERENCES")

    def draw(self, context):
        layout = self.layout
        root = _aitk(context)
        if root is None:
            layout.label(text="Not registered", icon="ERROR")
            return
        ui = root.ui

        # Density + tips toggles — three sibling toggles in one row.
        col = layout.column(align=True)
        col.prop(ui, "compact_mode", toggle=True, icon="ALIGN_JUSTIFY")
        col.prop(ui, "show_tips", toggle=True, icon="INFO")
        col.prop(ui, "show_advanced", toggle=True, icon="OPTIONS")

        layout.separator(factor=0.3)

        # Accent + theme as side-by-side dropdowns.
        params = layout.column(align=True)
        params.use_property_split = True
        params.use_property_decorate = False
        params.prop(ui, "accent_color", text="Accent")
        params.prop(ui, "theme_mode", text="Theme")
        params.prop(ui, "accent_custom", text="Color")

        layout.separator(factor=0.3)
        about = layout.column(align=True)
        about.scale_y = 0.9
        row = about.row(align=True)
        row.operator("aitk.open_docs", text="Docs", icon="HELP")
        row.operator("aitk.open_preferences", text="Preferences", icon="PREFERENCES")

        # Version footer — small muted line.
        ver = layout.row()
        ver.scale_y = 0.7
        ver.enabled = False
        ver.label(text="AI Toolkit · v1.0", icon="BLENDER")


__all__ = [
    "AITK_PT_root",
    "AITK_PT_text_to_3d",
    "AITK_PT_image_to_3d",
    "AITK_PT_texturing",
    "AITK_PT_render_preview",
    "AITK_PT_assistant",
    "AITK_PT_settings",
]
