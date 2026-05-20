"""AI Toolkit UI layer package.

All Blender-facing code (panels, operators, draw handlers, modal operators,
preferences) lives under this package. Modules here are free to import
``bpy`` and other Blender-distributed modules.
"""


"""AI Toolkit UI layer package."""

import bpy
from . import panels
from . import launcher_overlay

def register():
    # Находим настройки нашего аддона, чтобы передать их лаунчеру
    try:
        from ..services.settings.store import SettingsStore
        # Создаем экземпляр хранилища настроек, который требует лаунчер
        settings_store = SettingsStore()
        launcher_overlay.bind_settings(settings_store)
    except Exception as e:
        print(f"AI Toolkit UI: Не удалось привязать настройки к лаунчеру: {e}")

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
