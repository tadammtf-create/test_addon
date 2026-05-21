"""Shared enum lists used across the AI Toolkit sidebar.

Keeping every dropdown's items list here means the same string keys are
used everywhere (operator code, panel draw code, status labels), which
keeps the visual / stub layer consistent without scattering literals.

Each list is a 5-tuple ``(identifier, label, description, icon, number)``
matching :func:`bpy.props.EnumProperty`'s expected shape. Icons are
Blender's built-in icon names. The trailing integer is a stable unique
ID Blender requires when the items list also carries icons — bare
4-tuples ``(id, label, desc, icon)`` are interpreted as
``(id, label, desc, number)`` and fail registration because the icon
string is not an int.
"""

from __future__ import annotations

# ----------------------------------------------------------------------
# Shared presets
# ----------------------------------------------------------------------

QUALITY_PRESETS = (
    ("DRAFT", "Draft", "Fast preview, lower fidelity", "PARTICLE_POINT", 0),
    ("STANDARD", "Standard", "Balanced quality / speed", "PARTICLE_TIP", 1),
    ("HIGH", "High", "High fidelity, slower", "PARTICLE_PATH", 2),
    ("PRODUCTION", "Production", "Maximum quality, slowest", "RENDER_RESULT", 3),
)

STYLE_PRESETS = (
    ("REALISTIC", "Realistic", "Photoreal materials and topology", "MATSHADERBALL", 0),
    ("STYLIZED", "Stylized", "Hand-crafted, art-directed look", "BRUSH_DATA", 1),
    ("ANIME", "Anime", "Cel-shaded anime style", "OUTLINER_OB_LATTICE", 2),
    ("SCULPT", "Sculpted", "Organic sculpt forms", "SCULPTMODE_HLT", 3),
    ("LOWPOLY", "Low Poly", "Crisp low-polygon geometry", "MESH_ICOSPHERE", 4),
)

VIEW_PRESETS = (
    ("AUTO", "Auto", "Best guess from the input image", "AUTO", 0),
    ("FRONT", "Front", "Image is a front view", "VIEW_PERSPECTIVE", 1),
    ("THREE_QUARTER", "3/4", "Image is a three-quarter view", "ORIENTATION_GLOBAL", 2),
    ("ORTHO", "Orthographic", "Image is an orthographic projection", "VIEW_ORTHO", 3),
)

TEXTURE_STYLE = (
    ("PBR", "PBR", "Physically-based, production-ready", "MATSHADERBALL", 0),
    ("HANDPAINTED", "Hand-painted", "Hand-painted diffuse style", "BRUSH_DATA", 1),
    ("STYLIZED", "Stylized", "Art-directed stylised look", "MATERIAL", 2),
    ("WEATHERED", "Weathered", "Aged, weathered surfaces", "TEXTURE", 3),
)

TEXTURE_RES = (
    ("1K", "1K", "1024 x 1024", "IMAGE_DATA", 0),
    ("2K", "2K", "2048 x 2048", "IMAGE_DATA", 1),
    ("4K", "4K", "4096 x 4096", "IMAGE_DATA", 2),
)

RENDER_PRESETS = (
    ("CLAY", "Clay", "Neutral clay-render preview", "MATSPHERE", 0),
    ("STUDIO", "Studio", "Three-point studio lighting", "LIGHT_AREA", 1),
    ("OUTDOOR", "Outdoor", "HDRI outdoor lighting", "WORLD", 2),
    ("DRAMATIC", "Dramatic", "High-contrast dramatic lighting", "LIGHT_SPOT", 3),
    ("WIREFRAME", "Wireframe", "Technical wireframe overlay", "MOD_WIREFRAME", 4),
)

ASSISTANT_MODES = (
    ("CHAT", "Chat", "Conversational helper", "OUTLINER_DATA_GP_LAYER", 0),
    ("ANALYZE", "Analyse", "Inspect the active scene / object", "VIEWZOOM", 1),
    ("AUTOMATE", "Automate", "Run multi-step macros", "AUTO", 2),
)

ACCENT_COLORS = (
    ("BLUE", "Blue", "Blender-blue accent", "STRIP_COLOR_05", 0),
    ("ORANGE", "Orange", "Warm orange accent", "STRIP_COLOR_02", 1),
    ("GREEN", "Green", "Fresh green accent", "STRIP_COLOR_04", 2),
    ("PURPLE", "Purple", "Soft purple accent", "STRIP_COLOR_06", 3),
    ("PINK", "Pink", "Soft pink accent", "STRIP_COLOR_01", 4),
    ("YELLOW", "Yellow", "Bright yellow accent", "STRIP_COLOR_03", 5),
    ("NEUTRAL", "Neutral", "Match Blender's theme", "SHADING_SOLID", 6),
)

THEME_MODES = (
    ("AUTO", "Auto", "Follow Blender theme", "BLENDER", 0),
    ("DARK", "Dark", "Force dark appearance", "WORLD", 1),
    ("LIGHT", "Light", "Force light appearance", "LIGHT_SUN", 2),
)


# Map accent identifier -> built-in Blender icon used as a swatch.
# Lets panel code resolve a colour indicator without re-walking the
# tuple above. Index 3 is the icon string in our 5-tuple layout.
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
