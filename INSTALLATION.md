# Инструкция по установке AI Toolkit Platform для Blender

## Содержание
1. [Подготовка аддона к упаковке](#1-подготовка-аддона-к-упаковке)
2. [Создание ZIP-архива](#2-создание-zip-архива)
3. [Установка в Blender](#3-установка-в-blender)
4. [Первоначальная настройка](#4-первоначальная-настройка)
5. [Проверка работоспособности](#5-проверка-работоспособности)
6. [Решение проблем](#6-решение-проблем)

---

## 1. Подготовка аддона к упаковке

### 1.1. Проверьте структуру файлов

Убедитесь, что в корневой папке `ai_toolkit` присутствуют следующие обязательные файлы:

```
ai_toolkit/
├── __init__.py          # Точка входа аддона (обязательно!)
├── operators.py         # Legacy операторы
├── properties.py        # Legacy свойства
├── flux_1.py           # FLUX v1 провайдер
├── flux_2.py           # FLUX v2 провайдер
├── hunyuan3d_api.py    # Hunyuan3D провайдер
├── ui/                 # UI слой
│   ├── __init__.py
│   ├── theme.py
│   ├── theme.json
│   ├── icons/
│   │   ├── __init__.py
│   │   └── launcher_default.png
│   ├── panels/
│   ├── operators/
│   └── ...
└── services/           # Service слой (без bpy)
    ├── __init__.py
    ├── jobs/
    ├── providers/
    ├── models/
    ├── settings/
    ├── history/
    └── scene/
```

### 1.2. Проверьте файл `__init__.py`

Откройте `ai_toolkit/__init__.py` и убедитесь, что присутствует блок `bl_info`:

```python
bl_info = {
    "name": "AI Toolkit Platform",
    "author": "Your Name",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > AI Toolkit",
    "description": "AI-powered creative tools for Blender",
    "warning": "",
    "doc_url": "https://your-docs-url.com",
    "category": "3D View",
}
```

**Важно**: Если `bl_info` отсутствует, Blender не распознает папку как аддон!

### 1.3. Удалите ненужные файлы

Перед упаковкой удалите из папки `ai_toolkit`:
- `.git/` (если есть)
- `.gitignore`
- `__pycache__/` (все папки с кэшем Python)
- `.pyc` файлы
- `.kiro/` (папка со спецификациями - не нужна в релизе)
- `requirements.md`, `design.md`, `tasks.md` (документация разработки)
- Любые тестовые файлы

**Команда для очистки (выполните в корне проекта):**

```bash
# Windows PowerShell
Get-ChildItem -Path "ai_toolkit" -Recurse -Include "__pycache__","*.pyc" | Remove-Item -Recurse -Force

# Windows CMD
for /d /r "ai_toolkit" %d in (__pycache__) do @if exist "%d" rd /s /q "%d"
del /s /q "ai_toolkit\*.pyc"
```

---

## 2. Создание ZIP-архива

### Вариант A: Через проводник Windows

1. Откройте папку, **содержащую** `ai_toolkit` (не саму папку!)
2. Найдите папку `ai_toolkit`
3. Щелкните правой кнопкой мыши → **Отправить** → **Сжатая ZIP-папка**
4. Переименуйте созданный архив в `ai_toolkit.zip`

**Важно**: Структура архива должна быть:
```
ai_toolkit.zip
└── ai_toolkit/
    ├── __init__.py
    ├── ui/
    ├── services/
    └── ...
```

**НЕ ПРАВИЛЬНО** (папка внутри папки):
```
ai_toolkit.zip
└── some_folder/
    └── ai_toolkit/
        └── __init__.py
```

### Вариант B: Через командную строку

```bash
# Windows PowerShell (из родительской папки ai_toolkit)
Compress-Archive -Path "ai_toolkit" -DestinationPath "ai_toolkit.zip" -Force

# Или используя 7-Zip (если установлен)
7z a -tzip ai_toolkit.zip ai_toolkit\
```

### 2.1. Проверка архива

Откройте созданный `ai_toolkit.zip` и убедитесь:
- ✅ Первый уровень содержит папку `ai_toolkit/`
- ✅ Внутри `ai_toolkit/` есть `__init__.py`
- ✅ Размер архива разумный (обычно 1-5 МБ без больших ассетов)

---

## 3. Установка в Blender

### 3.1. Откройте Blender

Запустите Blender 4.0 или новее.

### 3.2. Откройте настройки

**Edit** → **Preferences** (или `Ctrl+Alt+U`)

### 3.3. Перейдите в раздел Add-ons

В левом меню выберите **Add-ons**

### 3.4. Установите аддон

1. Нажмите кнопку **Install...** в правом верхнем углу
2. В открывшемся диалоге выберите файл `ai_toolkit.zip`
3. Нажмите **Install Add-on**

### 3.5. Активируйте аддон

1. В поле поиска введите `AI Toolkit`
2. Найдите аддон **AI Toolkit Platform**
3. Поставьте галочку слева от названия

**Если аддон не появился:**
- Проверьте консоль Blender: **Window** → **Toggle System Console**
- Ищите ошибки импорта или синтаксиса

### 3.6. Сохраните настройки

Нажмите кнопку **Save Preferences** внизу окна настроек (иначе аддон отключится при перезапуске).

---

## 4. Первоначальная настройка

### 4.1. Откройте настройки аддона

В окне **Preferences** → **Add-ons**:
1. Найдите **AI Toolkit Platform**
2. Разверните его, нажав на стрелку слева

### 4.2. Настройте учетные данные провайдеров

Для каждого провайдера, который планируете использовать:

**FLUX (Text-to-Image):**
- Поле: `HuggingFace Token`
- Получить: https://huggingface.co/settings/tokens
- Создайте токен с правами `read`

**Hunyuan3D (Image-to-3D):**
- Обычно не требует токена (использует публичный Space)
- Если требуется, укажите в соответствующем поле

**OpenAI Chat (AI Assistant):**
- Поле: `API Key`
- Получить: https://platform.openai.com/api-keys
- Создайте новый API ключ

### 4.3. Проверьте соединение

Для каждого провайдера:
1. Введите учетные данные
2. Нажмите кнопку **Test Connection**
3. Дождитесь результата:
   - ✅ Зеленый "Connected" - всё работает
   - ❌ Красная ошибка - проверьте токен/ключ

### 4.4. Настройте параметры по умолчанию

**Launcher Settings:**
- `Enable Launcher Overlay` - показывать кнопку в viewport (рекомендуется ✅)
- `Launcher Offset X/Y` - позиция кнопки (по умолчанию 24, 24)

**Theme:**
- `Match Blender Theme` (рекомендуется) - автоматически подстраивается под тему Blender
- `Dark` / `Light` - фиксированная тема

**Default Providers:**
- Выберите предпочитаемого провайдера для каждой задачи

---

## 5. Проверка работоспособности

### 5.1. Проверьте Floating Launcher

1. Откройте **3D Viewport**
2. В левом нижнем углу должна появиться кнопка с иконкой AI Toolkit
3. Кликните на неё - должно открыться меню с 5 модулями:
   - Text-to-3D
   - Image-to-3D
   - AI Texturing
   - Render Preview
   - AI Assistant

**Если кнопка не появилась:**
- Проверьте `Enable Launcher Overlay` в настройках аддона
- Откройте N-panel (нажмите `N` в viewport) → вкладка **AI Toolkit**

### 5.2. Тест Text-to-3D

1. Откройте Launcher → **Text-to-3D**
2. Введите промпт: `a simple cube`
3. Нажмите **Generate 3D Model**
4. Дождитесь завершения (статус изменится на `succeeded`)
5. Модель должна появиться в сцене

### 5.3. Тест AI Assistant

1. Откройте Launcher → **AI Assistant**
2. Включите `Include scene context`
3. Введите: `What objects are in my scene?`
4. Нажмите **Send**
5. Должен появиться ответ от AI

### 5.4. Проверьте History

1. Откройте Launcher → **History** (или через N-panel)
2. Должны отображаться выполненные задачи
3. Попробуйте **Re-import** для успешной задачи

---

## 6. Решение проблем

### Проблема: Аддон не появляется в списке

**Решение:**
1. Проверьте структуру ZIP (см. раздел 2)
2. Убедитесь, что `__init__.py` содержит `bl_info`
3. Проверьте консоль Blender на ошибки импорта

### Проблема: Ошибка при активации аддона

**Решение:**
1. Откройте консоль: **Window** → **Toggle System Console**
2. Найдите строку с ошибкой (обычно `ImportError` или `SyntaxError`)
3. Проверьте, что все файлы `__init__.py` присутствуют в подпапках
4. Убедитесь, что используете Python 3.10+ (встроенный в Blender 4.0+)

### Проблема: "AI Toolkit is not yet ready"

**Решение:**
1. Перезапустите Blender
2. Убедитесь, что аддон активирован (галочка стоит)
3. Проверьте, что `register()` в `__init__.py` вызывает `bind_dependencies()`

### Проблема: Floating Launcher не отображается

**Решение:**
1. Preferences → Add-ons → AI Toolkit Platform → `Enable Launcher Overlay` ✅
2. Если не помогло, используйте N-panel: нажмите `N` в viewport → вкладка **AI Toolkit**

### Проблема: "No provider available for task"

**Решение:**
1. Проверьте, что провайдеры зарегистрированы: откройте консоль Python в Blender
2. Выполните:
   ```python
   import ai_toolkit.services.providers.registry as reg
   print(reg.ProviderRegistry._providers)
   ```
3. Если список пуст, проверьте файлы в `services/providers/`

### Проблема: Test Connection не работает

**Решение:**
1. Проверьте интернет-соединение
2. Убедитесь, что токен/ключ скопирован полностью (без пробелов)
3. Для HuggingFace: токен должен иметь права `read`
4. Для OpenAI: проверьте баланс аккаунта

### Проблема: Задачи зависают в статусе "queued"

**Решение:**
1. Проверьте, что `AITK_OT_job_dispatcher` запущен (должен стартовать автоматически)
2. Перезапустите Blender
3. Проверьте консоль на ошибки в worker threads

### Проблема: Импорт моделей не работает

**Решение:**
1. Убедитесь, что Blender поддерживает формат (GLB/GLTF/FBX/OBJ)
2. Проверьте, что файл существует и не поврежден
3. Проверьте права доступа к временной папке

---

## Дополнительная информация

### Расположение файлов аддона после установки

**Windows:**
```
C:\Users\<Username>\AppData\Roaming\Blender Foundation\Blender\<version>\scripts\addons\ai_toolkit\
```

**macOS:**
```
/Users/<Username>/Library/Application Support/Blender/<version>/scripts/addons/ai_toolkit/
```

**Linux:**
```
~/.config/blender/<version>/scripts/addons/ai_toolkit/
```

### Логи и отладка

**Включить подробные логи:**
1. Откройте консоль Python в Blender: **Scripting** workspace
2. Выполните:
   ```python
   import logging
   logging.getLogger("ai_toolkit").setLevel(logging.DEBUG)
   ```

**Просмотр логов:**
- Логи выводятся в System Console: **Window** → **Toggle System Console**

### История задач

Файл истории сохраняется в:
```
<Blender Config>/ai_toolkit/history.json
```

Максимум 500 записей, старые автоматически удаляются.

### Удаление аддона

1. **Edit** → **Preferences** → **Add-ons**
2. Найдите **AI Toolkit Platform**
3. Разверните и нажмите **Remove**
4. Перезапустите Blender

---

## Контрольный список установки

- [ ] Создан ZIP-архив с правильной структурой
- [ ] Аддон установлен через Preferences → Add-ons → Install
- [ ] Аддон активирован (галочка стоит)
- [ ] Настройки сохранены (Save Preferences)
- [ ] Учетные данные провайдеров введены
- [ ] Test Connection успешен для нужных провайдеров
- [ ] Floating Launcher отображается в viewport
- [ ] Тестовая задача выполнена успешно
- [ ] История задач работает

**Готово! Аддон установлен и готов к использованию.** 🎉

---

## Поддержка

Если возникли проблемы, не описанные в этой инструкции:
1. Проверьте System Console на ошибки
2. Включите DEBUG-логирование (см. выше)
3. Проверьте, что все зависимости установлены (requests, gradio_client и т.д.)
4. Убедитесь, что используете Blender 4.0 или новее
