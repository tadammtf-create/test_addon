# AI Toolkit Platform - Быстрый старт

## 🚀 Упаковка и установка за 5 минут

### Шаг 1: Автоматическая упаковка (рекомендуется)

```bash
# Запустите скрипт упаковки
python package_addon.py
```

Скрипт автоматически:
- ✅ Очистит кэш Python
- ✅ Проверит структуру аддона
- ✅ Создаст ZIP-архив
- ✅ Проверит корректность архива

**Результат:** Файл `dist/ai_toolkit.zip` готов к установке!

---

### Шаг 2: Ручная упаковка (если нужно)

1. **Очистите кэш:**
   ```powershell
   # Windows PowerShell
   Get-ChildItem -Path "ai_toolkit" -Recurse -Include "__pycache__","*.pyc" | Remove-Item -Recurse -Force
   ```

2. **Создайте ZIP:**
   - Откройте папку, содержащую `ai_toolkit/`
   - Правый клик на `ai_toolkit` → Отправить → Сжатая ZIP-папка
   - Переименуйте в `ai_toolkit.zip`

3. **Проверьте структуру:**
   ```
   ai_toolkit.zip
   └── ai_toolkit/
       ├── __init__.py  ← Обязательно!
       ├── ui/
       └── services/
   ```

---

### Шаг 3: Установка в Blender

1. **Откройте Blender 4.0+**

2. **Edit → Preferences → Add-ons**

3. **Install... → Выберите `ai_toolkit.zip`**

4. **Поставьте галочку** напротив "AI Toolkit Platform"

5. **Save Preferences**

---

### Шаг 4: Настройка

1. **Разверните аддон** в списке Add-ons

2. **Введите учетные данные:**

   **Для FLUX (Text-to-Image):**
   - Получите токен: https://huggingface.co/settings/tokens
   - Вставьте в поле `HuggingFace Token`
   - Нажмите **Test Connection**

   **Для OpenAI (AI Assistant):**
   - Получите ключ: https://platform.openai.com/api-keys
   - Вставьте в поле `API Key`
   - Нажмите **Test Connection**

3. **Готово!** ✅

---

### Шаг 5: Первый запуск

1. **Откройте 3D Viewport**

2. **Найдите кнопку AI Toolkit** в левом нижнем углу

3. **Кликните → Выберите модуль:**
   - 🎨 Text-to-3D
   - 🖼️ Image-to-3D
   - 🎭 AI Texturing
   - 🌟 Render Preview
   - 💬 AI Assistant

4. **Введите промпт и нажмите Generate!**

---

## 🔧 Решение проблем

### Кнопка не появилась?
- Проверьте: Preferences → Add-ons → AI Toolkit → `Enable Launcher Overlay` ✅
- Альтернатива: Нажмите `N` в viewport → вкладка **AI Toolkit**

### Ошибка при активации?
- Откройте: **Window → Toggle System Console**
- Найдите строку с ошибкой
- Проверьте, что `__init__.py` содержит `bl_info`

### "No provider available"?
- Убедитесь, что ввели учетные данные
- Нажмите **Test Connection** для каждого провайдера
- Проверьте интернет-соединение

### Задачи зависают?
- Перезапустите Blender
- Проверьте консоль на ошибки
- Убедитесь, что токены/ключи валидны

---

## 📚 Полная документация

Подробная инструкция: **[INSTALLATION.md](INSTALLATION.md)**

---

## ✅ Контрольный список

- [ ] Python скрипт упаковки выполнен успешно
- [ ] ZIP-архив создан в папке `dist/`
- [ ] Аддон установлен через Blender Preferences
- [ ] Аддон активирован (галочка стоит)
- [ ] Учетные данные введены
- [ ] Test Connection успешен
- [ ] Floating Launcher отображается
- [ ] Тестовая задача выполнена

**Всё готово? Начинайте творить с AI! 🎉**

---

## 🆘 Поддержка

Если что-то не работает:
1. Проверьте **System Console** (Window → Toggle System Console)
2. Включите DEBUG-логи:
   ```python
   import logging
   logging.getLogger("ai_toolkit").setLevel(logging.DEBUG)
   ```
3. Прочитайте полную инструкцию в **INSTALLATION.md**

---

## 📦 Структура проекта

```
ai_toolkit/                    # Корень аддона
├── __init__.py               # Точка входа (с bl_info)
├── operators.py              # Legacy операторы
├── properties.py             # Legacy свойства
├── flux_1.py                 # FLUX v1
├── flux_2.py                 # FLUX v2
├── hunyuan3d_api.py          # Hunyuan3D
│
├── ui/                       # UI слой (с bpy)
│   ├── __init__.py
│   ├── theme.py
│   ├── theme.json
│   ├── icons/
│   ├── panels/
│   │   ├── text_to_3d_panel.py
│   │   ├── image_to_3d_panel.py
│   │   ├── texturing_panel.py
│   │   ├── render_preview_panel.py
│   │   └── assistant_panel.py
│   └── operators/
│       ├── job_ops.py        # Основные операторы
│       ├── launcher_ops.py
│       └── history_ops.py
│
└── services/                 # Service слой (без bpy)
    ├── jobs/                 # Система задач
    ├── providers/            # AI провайдеры
    ├── models/               # Модели данных
    ├── settings/             # Настройки
    ├── history/              # История
    └── scene/                # Анализ сцены
```

---

**Версия:** 2.0.0  
**Совместимость:** Blender 4.0+  
**Лицензия:** См. LICENSE файл
