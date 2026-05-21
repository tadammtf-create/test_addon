"""PropertyGroup definitions for the AI Toolkit sidebar.

Each Generation_Module has its own PropertyGroup so per-module state is
namespaced cleanly under ``Scene.aitk.<module>``. A root
:class:`AITK_PG_root` PropertyGroup bundles them together with a shared
:class:`AITK_PG_ui_state` so panel code can reach everything through one
``context.scene.aitk`` pointer.

.. note::
    This module deliberately does **NOT** ``from __future__ import
    annotations``. Blender's :class:`bpy.types.PropertyGroup`
    registration walks ``__annotations__`` and expects the real
    :func:`bpy.props.*` descriptors. PEP 563 would stringify those
    annotations and silently break registration.
"""

import bpy

from . import presets


# ----------------------------------------------------------------------
# Shared UI state (theme / accent / compact mode / show advanced)
# ----------------------------------------------------------------------


class AITK_PG_recent_item(bpy.types.PropertyGroup):
    """One entry in the Quick Launcher's recent-actions list.

    Stored on :class:`AITK_PG_root` as a :class:`bpy.props.CollectionProperty`
    so the recent list survives a .blend save/load cycle. Each entry is
    intentionally tiny — three short strings — so a generous ring buffer
    barely costs anything.
    """

    text: bpy.props.StringProperty(name="Prompt", default="")
    module: bpy.props.StringProperty(name="Module", default="")
    label: bpy.props.StringProperty(name="When", default="just now")


class AITK_PG_ui_state(bpy.types.PropertyGroup):
    """Shared per-scene UI state: theme, accent, density toggles.

    Stored on the Scene (via :class:`AITK_PG_root`) so a user's
    accent-colour pick and compact-mode preference persist with the
    .blend file. Survives Blender restart through the regular .blend
    save/load cycle.
    """

    compact_mode: bpy.props.BoolProperty(
        name="Compact",
        description=(
            "Hide secondary labels, descriptions and status rows so the "
            "panel fits a narrow sidebar"
        ),
        default=False,
    )

    show_advanced: bpy.props.BoolProperty(
        name="Show Advanced",
        description="Expand the per-module advanced options block",
        default=False,
    )

    show_tips: bpy.props.BoolProperty(
        name="Show Tips",
        description="Show small inline help labels under inputs",
        default=True,
    )

    accent_color: bpy.props.EnumProperty(
        name="Accent",
        description="Highlight colour used across the panel header",
        items=presets.ACCENT_COLORS,
        default="BLUE",
    )

    theme_mode: bpy.props.EnumProperty(
        name="Theme",
        description="Visual theme preference for the addon UI",
        items=presets.THEME_MODES,
        default="AUTO",
    )

    accent_custom: bpy.props.FloatVectorProperty(
        name="Accent Color",
        description="Custom accent swatch, mirrors the chosen accent preset",
        subtype="COLOR",
        size=3,
        default=(0.31, 0.55, 1.0),
        min=0.0,
        max=1.0,
    )

    show_launcher_button: bpy.props.BoolProperty(
        name="Floating Launcher",
        description=(
            "Show the small AI Toolkit launcher button in the bottom-left "
            "corner of the 3D Viewport. Click it to open the sidebar."
        ),
        default=True,
    )

    launcher_offset_x: bpy.props.IntProperty(
        name="Launcher X",
        description="Pixels from the viewport's left edge to the launcher button",
        default=20,
        min=0,
        max=4096,
    )

    launcher_offset_y: bpy.props.IntProperty(
        name="Launcher Y",
        description="Pixels from the viewport's bottom edge to the launcher button",
        default=20,
        min=0,
        max=4096,
    )

    active_module: bpy.props.EnumProperty(
        name="Active Module",
        description="Last module the user opened from the Quick Launcher",
        items=(
            ("TEXT_TO_3D", "Text-to-3D", "Text-to-3D"),
            ("IMAGE_TO_3D", "Image-to-3D", "Image-to-3D"),
            ("TEXTURING", "AI Texturing", "AI Texturing"),
            ("RENDER_PREVIEW", "Render Preview", "Render Preview"),
            ("ASSISTANT", "AI Assistant", "AI Assistant"),
        ),
        default="TEXT_TO_3D",
    )


# ----------------------------------------------------------------------
# Text-to-3D
# ----------------------------------------------------------------------


class AITK_PG_text_to_3d(bpy.types.PropertyGroup):
    """State for the Text-to-3D Generation_Module."""

    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Describe the 3D model you want to generate",
        default="",
        maxlen=1000,
    )

    quality: bpy.props.EnumProperty(
        name="Quality",
        description="Generation quality preset",
        items=presets.QUALITY_PRESETS,
        default="STANDARD",
    )

    style: bpy.props.EnumProperty(
        name="Style",
        description="Visual style for the generated model",
        items=presets.STYLE_PRESETS,
        default="REALISTIC",
    )

    polycount: bpy.props.IntProperty(
        name="Target Polycount",
        description="Approximate target triangle count",
        default=20000,
        min=1000,
        max=200000,
        soft_min=2000,
        soft_max=80000,
        step=500,
    )

    enable_textures: bpy.props.BoolProperty(
        name="Generate Textures",
        description="Also generate albedo + normal maps for the result",
        default=True,
    )

    seed: bpy.props.IntProperty(
        name="Seed",
        description="Generator seed (-1 randomises every run)",
        default=-1,
        min=-1,
        max=2**31 - 1,
    )

    status: bpy.props.StringProperty(
        name="Status",
        default="Ready",
    )


# ----------------------------------------------------------------------
# Image-to-3D
# ----------------------------------------------------------------------


class AITK_PG_image_to_3d(bpy.types.PropertyGroup):
    """State for the Image-to-3D Generation_Module."""

    image_path: bpy.props.StringProperty(
        name="Image",
        description=(
            "Path to the source image — supported formats: "
            ".png .jpg .jpeg .webp"
        ),
        default="",
        subtype="FILE_PATH",
    )

    view_preset: bpy.props.EnumProperty(
        name="View",
        description="Camera angle of the source image",
        items=presets.VIEW_PRESETS,
        default="AUTO",
    )

    mesh_detail: bpy.props.FloatProperty(
        name="Mesh Detail",
        description="Higher values produce more polygons and sharper edges",
        default=0.6,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    symmetry: bpy.props.BoolProperty(
        name="Enforce Symmetry",
        description="Mirror geometry across the local X axis",
        default=False,
    )

    remove_background: bpy.props.BoolProperty(
        name="Remove Background",
        description="Auto-remove the source image background before reconstruction",
        default=True,
    )

    status: bpy.props.StringProperty(
        name="Status",
        default="Ready",
    )


# ----------------------------------------------------------------------
# AI Texturing
# ----------------------------------------------------------------------


class AITK_PG_texturing(bpy.types.PropertyGroup):
    """State for the AI Texturing Generation_Module."""

    target: bpy.props.PointerProperty(
        name="Target",
        description="Object to texture (defaults to the active object)",
        type=bpy.types.Object,
    )

    style: bpy.props.EnumProperty(
        name="Style",
        description="Material style",
        items=presets.TEXTURE_STYLE,
        default="PBR",
    )

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        description="Texture resolution",
        items=presets.TEXTURE_RES,
        default="2K",
    )

    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Describe the material (e.g. 'rusted iron, weathered')",
        default="",
        maxlen=400,
    )

    use_albedo: bpy.props.BoolProperty(
        name="Albedo",
        description="Generate the albedo / base-colour map",
        default=True,
    )

    use_normal: bpy.props.BoolProperty(
        name="Normal",
        description="Generate the normal map",
        default=True,
    )

    use_roughness: bpy.props.BoolProperty(
        name="Roughness",
        description="Generate the roughness map",
        default=True,
    )

    use_metallic: bpy.props.BoolProperty(
        name="Metallic",
        description="Generate the metallic map",
        default=False,
    )

    use_displacement: bpy.props.BoolProperty(
        name="Displacement",
        description="Generate a displacement map",
        default=False,
    )

    status: bpy.props.StringProperty(
        name="Status",
        default="Ready",
    )


# ----------------------------------------------------------------------
# Render Preview
# ----------------------------------------------------------------------


class AITK_PG_render_preview(bpy.types.PropertyGroup):
    """State for the Render Preview Generation_Module."""

    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Render preview look",
        items=presets.RENDER_PRESETS,
        default="STUDIO",
    )

    samples: bpy.props.IntProperty(
        name="Samples",
        description="Number of render samples",
        default=32,
        min=1,
        max=256,
        soft_max=128,
    )

    denoise: bpy.props.BoolProperty(
        name="Denoise",
        description="Apply AI denoiser to the preview output",
        default=True,
    )

    transparent_bg: bpy.props.BoolProperty(
        name="Transparent Background",
        description="Render with a transparent background",
        default=False,
    )

    resolution_pct: bpy.props.IntProperty(
        name="Resolution",
        description="Render resolution as a percentage of the scene setting",
        default=50,
        min=10,
        max=100,
        subtype="PERCENTAGE",
    )

    status: bpy.props.StringProperty(
        name="Status",
        default="Ready",
    )


# ----------------------------------------------------------------------
# AI Assistant
# ----------------------------------------------------------------------


class AITK_PG_assistant(bpy.types.PropertyGroup):
    """State for the AI Assistant Generation_Module."""

    mode: bpy.props.EnumProperty(
        name="Mode",
        description="Assistant interaction mode",
        items=presets.ASSISTANT_MODES,
        default="CHAT",
    )

    prompt: bpy.props.StringProperty(
        name="Ask",
        description="Ask the assistant a question or describe what to do",
        default="",
        maxlen=500,
    )

    last_response: bpy.props.StringProperty(
        name="Response",
        default=(
            "Hi — pick a mode and type a request. I can describe the "
            "scene, suggest improvements, or run multi-step actions."
        ),
    )

    auto_apply: bpy.props.BoolProperty(
        name="Auto-apply Suggestions",
        description="Automatically apply safe suggestions to the scene",
        default=False,
    )

    status: bpy.props.StringProperty(
        name="Status",
        default="Idle",
    )


# ----------------------------------------------------------------------
# Root group
# ----------------------------------------------------------------------


class AITK_PG_root(bpy.types.PropertyGroup):
    """Root PropertyGroup attached to :class:`bpy.types.Scene`.

    Sub-panels read their state through ``context.scene.aitk.<module>``
    so adding a new module only requires extending this group and the
    panel registration list.
    """

    ui: bpy.props.PointerProperty(type=AITK_PG_ui_state)
    text_to_3d: bpy.props.PointerProperty(type=AITK_PG_text_to_3d)
    image_to_3d: bpy.props.PointerProperty(type=AITK_PG_image_to_3d)
    texturing: bpy.props.PointerProperty(type=AITK_PG_texturing)
    render_preview: bpy.props.PointerProperty(type=AITK_PG_render_preview)
    assistant: bpy.props.PointerProperty(type=AITK_PG_assistant)

    recent: bpy.props.CollectionProperty(
        name="Recent",
        description="Recent generation actions surfaced in the Quick Launcher",
        type=AITK_PG_recent_item,
    )


__all__ = [
    "AITK_PG_ui_state",
    "AITK_PG_text_to_3d",
    "AITK_PG_image_to_3d",
    "AITK_PG_texturing",
    "AITK_PG_render_preview",
    "AITK_PG_assistant",
    "AITK_PG_recent_item",
    "AITK_PG_root",
]
