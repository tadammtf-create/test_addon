"""Shared enum lists used across the AI Toolkit sidebar.

Keeping every dropdown's items list here means the same string keys are
used everywhere (operator code, panel draw code, status labels), which
keeps the visual / stub layer consistent without scattering literals.

Each list is a 4-tuple ``(identifier, label, description, icon)``
matching :func:`bpy.props.EnumProperty`'s expected shape. Icons are
Blender's built-in icon names so the dropdown reads as a first-party
Blender control.
"""

from __future__ import annotations

# ----------------------------------------------------------------------
# Shared presets
# ----------------------------------------------------------------------

QUALITY_PRESETS = (
    ("DRAFT", "Draft", "Fast preview, lower fidelity", "PARTICLE_POINT"),
    ("STANDARD", "Standard", "Balanced quality / speed", "PARTICLE_TIP"),
    ("HIGH", "High", "High fidelity, slower", "PARTICLE_PATH"),
    ("PRODUCTION", "Production", "Maximum quality, slowest", "RENDER_RESULT"),
)

STYLE_PRESETS = (
    ("REALISTIC", "Realistic", "Photoreal materials and topology", "MATSHADERBALL"),
    ("STYLIZED", "Stylized", "Hand-crafted, art-directed look", "BRUSH_DATA"),
    ("ANIME", "Anime", "Cel-shaded anime style", "OUTLINER_OB_LATTICE"),
    ("SCULPT", "Sculpted", "Organic sculpt forms", "SCULPTMODE_HLT"),
    ("LOWPOLY", "Low Poly", "Crisp low-polygon geometry", "MESH_ICOSPHERE"),
)

VIEW_PRESETS = (
    ("AUTO", "Auto", "Best guess from the input image", "AUTO"),
    ("FRONT", "Front", "Image is a front view", "VIEW_PERSPECTIVE"),
    ("THREE_QUARTER", "3/4", "Image is a three-quarter view", "ORIENTATION_GLOBAL"),
    ("ORTHO", "Orthographic", "Image is an orthographic projection", "VIEW_ORTHO"),
)

TEXTURE_STYLE = (
    ("PBR", "PBR", "Physically-based, production-ready", "MATSHADERBALL"),
    ("HANDPAINTED", "Hand-painted", "Hand-painted diffuse style", "BRUSH_DATA"),
    ("STYLIZED", "Stylized", "Art-directed stylised look", "MATERIAL"),
    ("WEATHERED", "Weathered", "Aged, weathered surfaces", "TEXTURE"),
)

TEXTURE_RES = (
    ("1K", "1K", "1024 × 1024", "IMAGE_DATA"),
    ("2K", "2K", "2048 × 2048", "IMAGE_DATA"),
    ("4K", "4K", "4096 × 4096", "IMAGE_DATA"),
)

RENDER_PRESETS = (
    ("CLAY", "Clay", "Neutral clay-render preview", "MATSPHERE"),
    ("STUDIO", "Studio", "Three-point studio lighting", "LIGHT_AREA"),
    ("OUTDOOR", "Outdoor", "HDRI outdoor lighting", "WORLD"),
    ("DRAMATIC", "Dramatic", "High-contrast dramatic lighting", "LIGHT_SPOT"),
    ("WIREFRAME", "Wireframe", "Technical wireframe overlay", "MOD_WIREFRAME"),
)

ASSISTANT_MODES = (
    ("CHAT", "Chat", "Conversational helper", "OUTLINER_DATA_GP_LAYER"),
    ("ANALYZE", "Analyse", "Inspect the active scene / object", "VIEWZOOM"),
    ("AUTOMATE", "Automate", "Run multi-step macros", "AUTO"),
)

ACCENT_COLORS = (
    ("BLUE", "Blue", "Blender-blue accent", "SEQUENCE_COLOR_05"),
    ("ORANGE", "Orange", "Warm orange accent", "SEQUENCE_COLOR_02"),
    ("GREEN", "Green", "Fresh green accent", "SEQUENCE_COLOR_04"),
    ("PURPLE", "Purple", "Soft purple accent", "SEQUENCE_COLOR_06"),
    ("PINK", "Pink", "Soft pink accent", "SEQUENCE_COLOR_01"),
    ("YELLOW", "Yellow", "Bright yellow accent", "SEQUENCE_COLOR_03"),
    ("NEUTRAL", "Neutral", "Match Blender's theme", "SHADING_SOLID"),
)

THEME_MODES = (
    ("AUTO", "Auto", "Follow Blender theme", "BLENDER"),
    ("DARK", "Dark", "Force dark appearance", "WORLD"),
    ("LIGHT", "Light", "Force light appearance", "LIGHT_SUN"),
)


# Map accent identifier -> built-in Blender icon used as a swatch.
# Lets panel code resolve a colour indicator without re-walking the
# tuple above.
ACCENT_ICON_BY_KEY = {item[0]: item[3] for item in ACCENT_COLORS}


__all__ = [
    "QUALITY_PRESETS",
    "STYLE_PRESETS",
    "VIEW_PRESETS",
    "TEXTURE_STYLE",
    "TEXTURE_RES",
    "RENDER_PRESETS",
    "ASSISTANT_MODES",
    "ACCENT_COLORS",
    "THEME_MODES",
    "ACCENT_ICON_BY_KEY",
]
