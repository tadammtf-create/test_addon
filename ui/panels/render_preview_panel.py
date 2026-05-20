"""Render Preview PropertyGroup, panel, and 'Show panel' operator.

The Render_Preview Generation_Module produces an AI-generated preview image
based on a captured viewport screenshot, an optional text prompt (<= 2000
characters), and a chosen style preset. This module provides the UI shell:

* :class:`AITK_PG_render_preview` -- the per-scene PropertyGroup that holds
  the prompt, style preset, suggestion-mode toggle, current job status,
  and the file paths of the captured viewport screenshot and the returned
  preview image.
* :class:`AITK_OT_show_render_preview_panel` -- the dispatch operator the
  Launcher_Menu invokes to bring up the panel (id ``aitk.show_render_preview_panel``).
* :class:`AITK_PT_render_preview_panel` -- the View-3D sidebar panel that
  draws the prompt/style/suggestion controls, the Generate / Cancel
  controls, and -- once a job has succeeded -- the side-by-side comparison
  view loading both images via :func:`bpy.data.images.load` and rendering
  them in :meth:`bpy.types.UILayout.template_image_preview` (Requirement
  7.4, 7.6).

The submit, cancel, and Save Preview operators referenced by the panel
(``aitk.submit_render_preview``, ``aitk.cancel_render_preview``,
``aitk.save_preview``) live in tasks 19.2 / 19.3 -- the panel only needs
their ``bl_idname``s to render the buttons.

Validates: Requirements 7.1, 7.4, 7.6, 7.8, 7.9, 7.10, 7.13.
"""

from __future__ import annotations

import logging

import bpy

logger = logging.getLogger("ai_toolkit")


# Style presets exposed in the UI -- (identifier, display name, tooltip).
# Default preset is ``photoreal`` per Requirement 7.8.
STYLE_PRESETS = [
    ("cinematic", "Cinematic", "Dramatic lighting and color grading"),
    ("photoreal", "Photoreal", "Naturalistic, photorealistic look"),
    ("stylised", "Stylised", "Painterly, stylised treatment"),
    ("studio_lighting", "Studio Lighting", "Clean studio lighting setup"),
]


class AITK_PG_render_preview(bpy.types.PropertyGroup):
    """Per-scene state for the Render Preview module.

    The submit operator (task 19.2) is responsible for populating
    ``viewport_screenshot_path`` and ``preview_image_path`` after a job
    reaches terminal status; this PropertyGroup only declares the storage.
    """

    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Optional prompt guiding the AI render preview (max 2000 chars)",
        default="",
        maxlen=2000,
    )
    style_preset: bpy.props.EnumProperty(
        name="Style",
        description="Visual style preset for the preview",
        items=STYLE_PRESETS,
        default="photoreal",
    )
    suggestion_mode: bpy.props.BoolProperty(
        name="Suggestion Mode",
        description="Show textual suggestions returned by the provider",
        default=False,
    )
    status: bpy.props.StringProperty(
        name="Status",
        description="Current Generation_Job status label",
        default="idle",
    )
    failure_reason: bpy.props.StringProperty(
        name="Failure",
        description="Failure reason for the most recent terminal job",
        default="",
    )
    current_job_id: bpy.props.StringProperty(
        name="Job",
        description="job_id of the currently active Generation_Job",
        default="",
    )
    # Output paths populated by the submit operator (task 19.2).
    viewport_screenshot_path: bpy.props.StringProperty(default="")
    preview_image_path: bpy.props.StringProperty(default="")


class AITK_OT_show_render_preview_panel(bpy.types.Operator):
    """Dispatch operator that surfaces the Render Preview panel.

    The Launcher_Menu invokes this when the user picks ``Render_Preview``;
    the panel itself lives in the standard 3D Viewport "AI Toolkit" sidebar
    tab and so the operator merely flags the area for redraw and reports a
    success status. Actual panel visibility is governed by Blender's panel
    system once :class:`AITK_PT_render_preview_panel` is registered.
    """

    bl_idname = "aitk.show_render_preview_panel"
    bl_label = "Show Render Preview"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            area = getattr(context, "area", None)
            if area is not None and getattr(area, "tag_redraw", None) is not None:
                area.tag_redraw()
        except Exception:
            logger.debug("Render preview: tag_redraw failed", exc_info=True)
        return {"FINISHED"}


class AITK_PT_render_preview_panel(bpy.types.Panel):
    """Sidebar panel for the Render Preview Generation_Module.

    The panel is sized and laid out per the platform's UI conventions: it
    appears in the View-3D N-panel under the "AI Toolkit" tab, exposes the
    prompt / style / suggestion controls, the job-state-aware Generate and
    Cancel buttons, and -- once a job succeeds -- a side-by-side
    comparison box.
    """

    bl_idname = "AITK_PT_render_preview_panel"
    bl_label = "Render Preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    def draw(self, context):
        layout = self.layout

        # Defensive lookup: the addon entry-point (task 24.1) attaches the
        # PropertyGroup to ``Scene.aitk_render_preview``, but draw() may
        # fire before that wiring is in place (for example during partial
        # registration after a reload). In that case we render a tiny
        # placeholder rather than letting an AttributeError disable the
        # panel.
        scene = getattr(context, "scene", None)
        props = getattr(scene, "aitk_render_preview", None) if scene is not None else None
        if props is None:
            layout.label(text="Render Preview unavailable", icon="ERROR")
            return

        layout.prop(props, "prompt", text="Prompt")
        layout.prop(props, "style_preset", text="Style")
        layout.prop(props, "suggestion_mode", text="Suggestion Mode")

        status = props.status
        row = layout.row()
        row.enabled = status in ("idle", "succeeded", "failed", "cancelled")
        row.operator("aitk.submit_render_preview", text="Generate", icon="RENDER_STILL")

        layout.label(text=f"Status: {status}", icon="INFO")
        if props.failure_reason:
            layout.label(text=f"Error: {props.failure_reason}", icon="ERROR")

        cancel_row = layout.row()
        cancel_row.enabled = status in ("queued", "running")
        cancel_row.operator("aitk.cancel_render_preview", text="Cancel", icon="CANCEL")

        # Side-by-side comparison view (Requirements 7.4, 7.6, 7.10, 7.13).
        if status == "succeeded" and (props.viewport_screenshot_path or props.preview_image_path):
            box = layout.box()
            box.label(text="Comparison:", icon="IMAGE_DATA")

            grid = box.grid_flow(row_major=True, columns=2, align=True)

            # Left column: viewport screenshot.
            left = grid.column()
            left.label(text="Original")
            if props.viewport_screenshot_path:
                self._draw_image(left, props.viewport_screenshot_path, label="screenshot")
            else:
                left.label(text="Screenshot unavailable")

            # Right column: AI preview.
            right = grid.column()
            right.label(text="AI Preview")
            if props.preview_image_path:
                self._draw_image(right, props.preview_image_path, label="preview")
            else:
                right.label(text="Preview unavailable")

            # Display the prompt and style preset alongside the result image
            # (Requirement 7.10).
            box.label(text=f"Prompt: {props.prompt[:80]}" if props.prompt else "Prompt: (none)")
            box.label(text=f"Style: {props.style_preset}")

            # Save Preview operator (Requirement 7.7 -- operator lives in
            # task 19.3; the panel only references the bl_idname).
            box.operator("aitk.save_preview", text="Save Preview", icon="EXPORT")

        # Suggestion mode placeholder (Requirement 7.9, 7.13).
        if props.suggestion_mode and status == "succeeded":
            sb = layout.box()
            sb.label(text="Suggested improvements:")
            sb.label(text="(no suggestions)", icon="INFO")

    def _draw_image(self, layout, path, *, label):
        """Best-effort image preview via :func:`bpy.data.images.load`.

        The panel is rendered every redraw so we rely on
        ``check_existing=True`` to avoid duplicate Image datablocks. We try
        :meth:`UILayout.template_image_preview` first (it produces a real
        rendered preview) and degrade to a path label on any failure --
        the comparison box must always show *something* for each slot
        (Requirement 7.6).
        """
        try:
            image = bpy.data.images.load(path, check_existing=True)
            if hasattr(layout, "template_image_preview"):
                try:
                    layout.template_image_preview(image)
                    return
                except Exception:
                    # Some Blender builds reject template_image_preview when
                    # the image has not been loaded into VRAM yet. Fall
                    # through to the path label so the user still sees the
                    # output location.
                    pass
            layout.label(text=f"{label}: {path}")
        except Exception:
            logger.debug("Render preview: failed to load %s", path, exc_info=True)
            layout.label(text=f"{label} unavailable", icon="ERROR")


__all__ = [
    "AITK_PG_render_preview",
    "AITK_OT_show_render_preview_panel",
    "AITK_PT_render_preview_panel",
    "STYLE_PRESETS",
]
