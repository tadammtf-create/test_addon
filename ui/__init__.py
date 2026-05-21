"""AI Toolkit UI layer package.

All Blender-facing code (panels, operators, draw handlers, modal operators,
preferences) lives under this package. Modules here are free to import
``bpy`` and other Blender-distributed modules.
"""


"""AI Toolkit UI layer package."""

import logging

import bpy
from . import panels
from . import launcher_overlay

logger = logging.getLogger("ai_toolkit")

def register():
    # Bind settings BEFORE launcher_overlay.register() so the draw
    # handler never observes a None _settings on its first redraw.
    try:
        from ..services.settings.store import SettingsStore
        settings_store = SettingsStore()
        launcher_overlay.bind_settings(settings_store)
    except Exception as e:  # noqa: BLE001 -- never abort addon register
        # Route through the addon-wide logger so failures are visible in
        # Blender's regular log pipeline, not just stdout.
        logger.warning(
            "Failed to bind SettingsStore to launcher overlay (%s: %s); "
            "the floating launcher will be hidden until the next reload.",
            type(e).__name__,
            e,
        )

    # Регистрируем панели интерфейса
    if hasattr(panels, "register"):
        panels.register()

    # Регистрируем плавающий лаунчер (иконку)
    if hasattr(launcher_overlay, "register"):
        launcher_overlay.register()

def unregister():
    if hasattr(panels, "unregister"):
        panels.unregister()
    if hasattr(launcher_overlay, "unregister"):
        launcher_overlay.unregister()
