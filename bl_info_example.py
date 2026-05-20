"""
Пример bl_info для ai_toolkit/__init__.py

Скопируйте этот блок в начало вашего __init__.py файла
(после docstring, но перед импортами).
"""

bl_info = {
    # Название аддона (отображается в списке Add-ons)
    "name": "AI Toolkit Platform",
    
    # Автор (ваше имя или ник)
    "author": "Your Name",
    
    # Версия аддона (major, minor, patch)
    # Увеличивайте при каждом релизе
    "version": (2, 0, 0),
    
    # Минимальная версия Blender (major, minor, patch)
    # Аддон не будет работать на более старых версиях
    "blender": (4, 0, 0),
    
    # Расположение в UI (подсказка для пользователя)
    "location": "View3D > AI Toolkit (bottom-left button)",
    
    # Краткое описание (1-2 предложения)
    "description": (
        "AI-powered creative tools: Text-to-3D, Image-to-3D, "
        "AI Texturing, Render Preview, and AI Assistant"
    ),
    
    # Предупреждение (опционально, для beta/alpha версий)
    # Оставьте пустым для стабильных релизов
    "warning": "",
    
    # Ссылка на документацию (опционально)
    "doc_url": "https://github.com/yourusername/ai-toolkit-platform",
    
    # Ссылка на баг-трекер (опционально)
    "tracker_url": "https://github.com/yourusername/ai-toolkit-platform/issues",
    
    # Категория в списке Add-ons
    # Возможные значения: "3D View", "Add Mesh", "Animation", "Development",
    # "Import-Export", "Material", "Mesh", "Node", "Object", "Paint",
    # "Render", "Rigging", "Scene", "Sculpt", "Sequencer", "System",
    # "Text Editor", "UV", "User Interface"
    "category": "3D View",
}


# ============================================================================
# Полный пример __init__.py с bl_info
# ============================================================================

"""
AI Toolkit Platform - Blender Addon

Integrates multiple AI-powered creative tools directly into the 3D Viewport.
"""

bl_info = {
    "name": "AI Toolkit Platform",
    "author": "Your Name",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > AI Toolkit (bottom-left button)",
    "description": (
        "AI-powered creative tools: Text-to-3D, Image-to-3D, "
        "AI Texturing, Render Preview, and AI Assistant"
    ),
    "warning": "",
    "doc_url": "https://github.com/yourusername/ai-toolkit-platform",
    "category": "3D View",
}


import logging
import bpy

# Настройка логгера
logger = logging.getLogger("ai_toolkit")
logger.setLevel(logging.INFO)

# Импорты из service layer
from .services.jobs.executor import JobExecutor
from .services.jobs.callbacks import CallbackQueue
from .services.providers.registry import ProviderRegistry
from .services.settings.store import SettingsStore
from .services.history.manager import HistoryManager

# Импорты из UI layer
from .ui.asset_importer import AssetImporter
from .ui.operators import job_ops, launcher_ops, history_ops
from .ui.panels import (
    text_to_3d_panel,
    image_to_3d_panel,
    texturing_panel,
    render_preview_panel,
    assistant_panel,
    history_panel,
)
from .ui.preferences import AITKAddonPreferences
from .ui.job_dispatcher import AITK_OT_job_dispatcher
from .ui.launcher_overlay import register_launcher, unregister_launcher


# Глобальные ссылки на сервисы (инициализируются в register())
_executor = None
_callbacks = None
_registry = None
_settings = None
_history = None
_asset_importer = None


def register():
    """Регистрация аддона в Blender."""
    global _executor, _callbacks, _registry, _settings, _history, _asset_importer
    
    logger.info("Registering AI Toolkit Platform...")
    
    try:
        # 1. Инициализация сервисов
        _settings = SettingsStore()
        _callbacks = CallbackQueue()
        _history = HistoryManager()
        _registry = ProviderRegistry(_settings, logger)
        _asset_importer = AssetImporter()
        _executor = JobExecutor(_registry, _callbacks, _history, logger)
        
        # 2. Обнаружение провайдеров
        _registry.discover("ai_toolkit.services.providers")
        
        # 3. Привязка зависимостей к операторам
        job_ops.bind_dependencies(_executor, _callbacks, _asset_importer, _history)
        
        # 4. Регистрация классов Blender
        bpy.utils.register_class(AITKAddonPreferences)
        bpy.utils.register_class(AITK_OT_job_dispatcher)
        
        # Регистрация операторов
        for cls in job_ops.__all__:
            bpy.utils.register_class(getattr(job_ops, cls))
        
        for cls in launcher_ops.__all__:
            bpy.utils.register_class(getattr(launcher_ops, cls))
        
        for cls in history_ops.__all__:
            bpy.utils.register_class(getattr(history_ops, cls))
        
        # Регистрация панелей
        # ... (регистрация всех панелей)
        
        # 5. Регистрация Floating Launcher
        register_launcher()
        
        # 6. Запуск Job Dispatcher
        bpy.ops.aitk.job_dispatcher()
        
        logger.info("AI Toolkit Platform registered successfully")
        
    except Exception as e:
        logger.error(f"Failed to register AI Toolkit Platform: {e}", exc_info=True)
        raise


def unregister():
    """Отмена регистрации аддона."""
    global _executor, _callbacks, _registry, _settings, _history, _asset_importer
    
    logger.info("Unregistering AI Toolkit Platform...")
    
    try:
        # 1. Остановка Job Dispatcher и Executor
        if _executor is not None:
            _executor.shutdown(timeout=5.0)
        
        # 2. Удаление Floating Launcher
        unregister_launcher()
        
        # 3. Отмена регистрации классов Blender
        # ... (в обратном порядке)
        
        # 4. Очистка глобальных ссылок
        _executor = None
        _callbacks = None
        _registry = None
        _settings = None
        _history = None
        _asset_importer = None
        
        logger.info("AI Toolkit Platform unregistered successfully")
        
    except Exception as e:
        logger.error(f"Failed to unregister AI Toolkit Platform: {e}", exc_info=True)


# Точка входа для Blender
if __name__ == "__main__":
    register()


# ============================================================================
# Примечания
# ============================================================================

"""
ВАЖНО:

1. bl_info ОБЯЗАТЕЛЕН для распознавания аддона Blender
2. bl_info должен быть на уровне модуля (не внутри функции)
3. bl_info должен быть словарем с определенными ключами
4. Минимально необходимые ключи: name, author, version, blender, category
5. version и blender должны быть кортежами из 3 целых чисел

РЕКОМЕНДАЦИИ:

1. Увеличивайте version при каждом релизе:
   - (2, 0, 0) -> (2, 0, 1) - патч (багфиксы)
   - (2, 0, 1) -> (2, 1, 0) - минор (новые функции)
   - (2, 1, 0) -> (3, 0, 0) - мажор (breaking changes)

2. Указывайте реальную минимальную версию Blender:
   - Если используете API из Blender 4.0, укажите (4, 0, 0)
   - Если работает на 3.6+, можете указать (3, 6, 0)

3. Выбирайте подходящую категорию:
   - "3D View" - для инструментов viewport
   - "Import-Export" - для импортеров/экспортеров
   - "Add Mesh" - для генераторов мешей
   - и т.д.

4. Заполните doc_url и tracker_url для удобства пользователей

5. Используйте warning только для нестабильных версий:
   - "Beta version - may contain bugs"
   - "Requires internet connection"
   - "Experimental features"
"""
