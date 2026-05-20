# AI Toolkit Platform для Blender

> Мощная платформа AI-инструментов, интегрированная непосредственно в 3D Viewport Blender

[![Blender](https://img.shields.io/badge/Blender-4.0+-orange.svg)](https://www.blender.org/)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🎯 Что это?

**AI Toolkit Platform** — это полностью переработанный Blender аддон, который объединяет несколько AI-инструментов в единую модульную платформу с чистой архитектурой и интуитивным интерфейсом.

### ✨ Ключевые возможности

- 🎨 **Text-to-3D** — Генерация 3D моделей из текстового описания
- 🖼️ **Image-to-3D** — Преобразование изображений в 3D объекты
- 🎭 **AI Texturing** — Автоматическая генерация текстур для моделей
- 🌟 **Render Preview** — AI-превью финального рендера
- 💬 **AI Assistant** — Интеллектуальный помощник с анализом сцены

### 🚀 Особенности

- **Floating Launcher** — Удобная кнопка прямо в viewport
- **Модульная архитектура** — Легко добавлять новые AI провайдеры
- **Многопоточность** — До 4 одновременных задач
- **История задач** — Все результаты сохраняются и доступны для повторного импорта
- **Чистая архитектура** — Разделение UI и Service слоев

---

## 📦 Быстрая установка

### Шаг 1: Упаковка

```bash
# Автоматическая упаковка (рекомендуется)
python package_addon.py

# Результат: dist/ai_toolkit.zip
```

### Шаг 2: Установка в Blender

1. Откройте **Blender 4.0+**
2. **Edit** → **Preferences** → **Add-ons**
3. Нажмите **Install...** → Выберите `ai_toolkit.zip`
4. Поставьте **галочку** напротив "AI Toolkit Platform"
5. Нажмите **Save Preferences**

### Шаг 3: Настройка

1. Разверните аддон в списке Add-ons
2. Введите учетные данные для провайдеров:
   - **HuggingFace Token** (для FLUX): https://huggingface.co/settings/tokens
   - **OpenAI API Key** (для AI Assistant): https://platform.openai.com/api-keys
3. Нажмите **Test Connection** для каждого провайдера

### Шаг 4: Готово! 🎉

Откройте **3D Viewport** и найдите кнопку **AI Toolkit** в левом нижнем углу.

---

## 📚 Документация

| Документ | Описание |
|----------|----------|
| **[QUICK_START.md](QUICK_START.md)** | Быстрый старт за 5 минут |
| **[INSTALLATION.md](INSTALLATION.md)** | Подробная инструкция по установке |
| **[INSTALLATION_FLOWCHART.md](INSTALLATION_FLOWCHART.md)** | Визуальная блок-схема процесса |
| **[PRE_RELEASE_CHECKLIST.md](PRE_RELEASE_CHECKLIST.md)** | Чеклист перед релизом |
| **[PACKAGING_SUMMARY.md](PACKAGING_SUMMARY.md)** | Краткое резюме упаковки |

---

## 🏗️ Архитектура

### Структура проекта

```
ai_toolkit/
├── __init__.py              # Точка входа (с bl_info)
├── operators.py             # Legacy операторы
├── properties.py            # Legacy свойства
├── flux_1.py               # FLUX v1 провайдер
├── flux_2.py               # FLUX v2 провайдер
├── hunyuan3d_api.py        # Hunyuan3D провайдер
│
├── ui/                     # UI Layer (с bpy)
│   ├── theme.py           # Система тем
│   ├── theme.json         # Определение темы
│   ├── icons/             # Иконки
│   ├── panels/            # Панели модулей
│   │   ├── text_to_3d_panel.py
│   │   ├── image_to_3d_panel.py
│   │   ├── texturing_panel.py
│   │   ├── render_preview_panel.py
│   │   └── assistant_panel.py
│   ├── operators/         # Операторы
│   │   ├── job_ops.py    # Основные операторы задач
│   │   ├── launcher_ops.py
│   │   └── history_ops.py
│   ├── launcher_overlay.py  # Floating Launcher
│   ├── launcher_menu.py     # Launcher Menu
│   ├── job_dispatcher.py    # Modal timer dispatcher
│   └── asset_importer.py    # Импорт результатов
│
└── services/               # Service Layer (без bpy)
    ├── jobs/              # Система задач
    │   ├── job.py        # Модель задачи
    │   ├── executor.py   # Многопоточный исполнитель
    │   ├── handle.py     # Хэндл задачи
    │   └── callbacks.py  # Очередь коллбэков
    ├── providers/         # AI провайдеры
    │   ├── base.py       # Базовый интерфейс
    │   ├── registry.py   # Реестр провайдеров
    │   ├── flux_v1.py    # FLUX v1
    │   ├── flux_v2.py    # FLUX v2
    │   ├── hunyuan3d.py  # Hunyuan3D
    │   └── chat_openai.py # OpenAI Chat
    ├── models/            # Модели данных
    │   ├── requests.py   # Request dataclasses
    │   └── responses.py  # Response dataclasses
    ├── settings/          # Настройки
    │   ├── store.py      # Хранилище настроек
    │   └── schema.py     # Схема настроек
    ├── history/           # История задач
    │   └── manager.py    # Менеджер истории
    ├── scene/             # Анализ сцены
    │   └── analyzer.py   # Анализатор сцены
    └── chat.py            # Утилиты чата
```

### Принципы архитектуры

1. **Разделение слоев**
   - **UI Layer** — Весь код с `bpy`, панели, операторы
   - **Service Layer** — Чистая бизнес-логика без `bpy`

2. **Модульность**
   - Провайдеры регистрируются автоматически
   - Легко добавлять новые AI сервисы

3. **Многопоточность**
   - ThreadPool для параллельных задач
   - CallbackQueue для безопасного обновления UI

4. **Типобезопасность**
   - Dataclasses для всех моделей данных
   - Никаких `bpy.types` в Service Layer

---

## 🎨 Модули

### 1. Text-to-3D

Генерация 3D моделей из текстового описания.

**Использование:**
1. Launcher → Text-to-3D
2. Введите промпт (например: "a simple cube")
3. Нажмите "Generate 3D Model"
4. Модель автоматически импортируется в сцену

**Провайдеры:** Hunyuan3D (через промежуточную генерацию изображения)

---

### 2. Image-to-3D

Преобразование изображений в 3D объекты.

**Использование:**
1. Launcher → Image-to-3D
2. Выберите изображение (.png, .jpg, .jpeg, .webp)
3. Нажмите "Generate 3D from Image"
4. Модель автоматически импортируется в сцену

**Ограничения:**
- Максимальный размер файла: 20 МБ
- Поддерживаемые форматы: PNG, JPG, JPEG, WebP

**Провайдеры:** Hunyuan3D

---

### 3. AI Texturing

Генерация текстур для выбранного объекта.

**Использование:**
1. Выберите **один** mesh объект
2. Launcher → AI Texturing
3. Введите описание материала
4. Выберите типы текстур (base_color, normal, roughness, metallic)
5. Нажмите "Generate Texture"
6. Материал автоматически создается и применяется

**Требования:**
- Ровно один выбранный mesh объект
- Рекомендуется наличие UV-карты

**Провайдеры:** Настраиваемые (по умолчанию через FLUX)

---

### 4. Render Preview

AI-превью финального рендера на основе viewport.

**Использование:**
1. Настройте сцену в viewport
2. Launcher → Render Preview
3. (Опционально) Введите промпт для стиля
4. Выберите стиль: Cinematic, Photoreal, Stylised, Studio Lighting
5. Нажмите "Generate Render Preview"
6. Сравните оригинал и AI-превью side-by-side

**Возможности:**
- Захват текущего viewport
- Анализ сцены (объекты, освещение, материалы)
- Режим предложений (AI советы по улучшению)
- Сохранение превью в файл

**Провайдеры:** Настраиваемые (обычно через FLUX или Stable Diffusion)

---

### 5. AI Assistant

Интеллектуальный помощник с пониманием контекста сцены.

**Использование:**
1. Launcher → AI Assistant
2. (Опционально) Включите "Include scene context"
3. Введите вопрос или команду
4. Нажмите "Send"
5. Получите ответ с учетом вашей сцены

**Возможности:**
- Анализ текущей сцены
- Советы по моделированию и освещению
- Помощь с Blender API
- Поддержка стриминга ответов
- Сохранение истории разговора в .blend файле

**Провайдеры:** OpenAI GPT, Anthropic Claude, или другие chat API

---

## 🔧 Настройки

### Launcher Settings

- **Enable Launcher Overlay** — Показывать кнопку в viewport
- **Launcher Offset X/Y** — Позиция кнопки (пиксели от нижнего левого угла)
- **Custom Icon** — Путь к кастомной иконке (опционально)

### Theme

- **Match Blender Theme** (рекомендуется) — Автоматическая подстройка
- **Dark** — Темная тема
- **Light** — Светлая тема

### Default Providers

Выберите предпочитаемого провайдера для каждой задачи:
- Text-to-Image
- Image-to-3D
- Text-to-3D
- Texture Generation
- Render Preview
- Chat Completion

### Credentials

Введите учетные данные для каждого провайдера:
- **HuggingFace Token** — Для FLUX (text-to-image)
- **OpenAI API Key** — Для AI Assistant
- Другие провайдеры по мере добавления

---

## 📊 История задач

Все выполненные задачи сохраняются в истории.

**Доступ:** Launcher → History (или N-panel → AI Toolkit → History)

**Возможности:**
- Просмотр всех задач (до 500 последних)
- Re-import успешных результатов
- Удаление записей
- Фильтрация по статусу

**Расположение файла:**
```
<Blender Config>/ai_toolkit/history.json
```

---

## 🐛 Решение проблем

### Аддон не появляется в списке

**Причина:** Отсутствует или некорректен `bl_info` в `__init__.py`

**Решение:**
1. Проверьте наличие `bl_info` в начале `__init__.py`
2. Убедитесь, что все обязательные поля заполнены
3. См. `bl_info_example.py` для примера

---

### Ошибка при активации

**Причина:** Ошибка импорта или синтаксиса

**Решение:**
1. Откройте System Console: **Window** → **Toggle System Console**
2. Найдите строку с ошибкой (обычно `ImportError` или `SyntaxError`)
3. Проверьте, что все `__init__.py` присутствуют в подпапках
4. Убедитесь, что используете Blender 4.0+

---

### Floating Launcher не отображается

**Причина:** Отключен в настройках

**Решение:**
1. Preferences → Add-ons → AI Toolkit Platform
2. Убедитесь, что `Enable Launcher Overlay` ✅
3. Альтернатива: Нажмите `N` в viewport → вкладка **AI Toolkit**

---

### "No provider available for task"

**Причина:** Провайдер не зарегистрирован или не настроен

**Решение:**
1. Проверьте, что учетные данные введены
2. Нажмите **Test Connection** для провайдера
3. Убедитесь, что провайдер поддерживает нужную задачу
4. Проверьте консоль на ошибки регистрации

---

### Задачи зависают в статусе "queued"

**Причина:** Job Dispatcher не запущен или зависла очередь

**Решение:**
1. Перезапустите Blender
2. Проверьте консоль на ошибки
3. Убедитесь, что `AITK_OT_job_dispatcher` запущен

---

### Test Connection не работает

**Причина:** Неверные учетные данные или проблемы с сетью

**Решение:**
1. Проверьте интернет-соединение
2. Убедитесь, что токен/ключ скопирован полностью (без пробелов)
3. Для HuggingFace: токен должен иметь права `read`
4. Для OpenAI: проверьте баланс аккаунта
5. Попробуйте создать новый токен/ключ

---

### Импорт моделей не работает

**Причина:** Неподдерживаемый формат или поврежденный файл

**Решение:**
1. Убедитесь, что формат поддерживается (GLB, GLTF, FBX, OBJ)
2. Проверьте, что файл существует и не поврежден
3. Проверьте права доступа к временной папке
4. Попробуйте импортировать файл вручную через File → Import

---

## 🔍 Отладка

### Включение DEBUG-логов

```python
# В Scripting workspace или Python Console
import logging
logging.getLogger("ai_toolkit").setLevel(logging.DEBUG)
```

### Просмотр логов

Логи выводятся в **System Console**: **Window** → **Toggle System Console**

### Проверка состояния провайдеров

```python
# В Python Console
import ai_toolkit.services.providers.registry as reg
print(reg.ProviderRegistry._providers)
```

### Проверка активных задач

```python
# В Python Console
from ai_toolkit.ui.operators import job_ops
print(job_ops._handles)
```

---

## 🚀 Разработка

### Добавление нового провайдера

1. Создайте файл в `services/providers/my_provider.py`
2. Наследуйте `AIProvider` из `base.py`
3. Реализуйте все абстрактные методы
4. Провайдер автоматически зарегистрируется при запуске

**Пример:**

```python
from .base import AIProvider, CredentialField

class MyProvider(AIProvider):
    def get_id(self) -> str:
        return "my_provider"
    
    def get_display_name(self) -> str:
        return "My AI Provider"
    
    def get_supported_tasks(self) -> list[str]:
        return ["text_to_image"]
    
    def get_required_credentials(self) -> list[CredentialField]:
        return [
            CredentialField(
                key="api_key",
                label="API Key",
                is_password=True,
                description="Your API key"
            )
        ]
    
    def validate_credentials(self, credentials: dict) -> bool:
        # Проверка учетных данных
        return True
    
    def submit_job(self, task, request, credentials, on_status, cancel_event):
        # Выполнение задачи
        pass
```

---

## 📝 Требования

### Системные требования

- **Blender:** 4.0 или новее
- **Python:** 3.10+ (встроен в Blender)
- **ОС:** Windows 10/11, macOS 10.15+, Linux
- **Интернет:** Требуется для работы AI провайдеров

### Python зависимости

Устанавливаются автоматически при первом запуске:
- `requests` — HTTP клиент
- `gradio_client` — Для Hunyuan3D
- Другие зависимости по мере добавления провайдеров

---

## 📄 Лицензия

MIT License — см. файл [LICENSE](LICENSE)

---

## 🤝 Вклад в проект

Приветствуются:
- Баг-репорты
- Запросы новых функций
- Pull requests
- Новые AI провайдеры
- Улучшения документации

---

## 📞 Поддержка

- **GitHub Issues:** [Создать issue](https://github.com/yourusername/ai-toolkit-platform/issues)
- **Документация:** См. файлы в корне проекта
- **Email:** your.email@example.com

---

## 🙏 Благодарности

- **Blender Foundation** — За потрясающий 3D редактор
- **HuggingFace** — За FLUX и Hunyuan3D
- **OpenAI** — За GPT API
- **Сообщество Blender** — За поддержку и фидбэк

---

## 📈 Roadmap

### Версия 2.1 (планируется)

- [ ] Поддержка Stable Diffusion XL
- [ ] Batch processing для множественных задач
- [ ] Кастомные пресеты для каждого модуля
- [ ] Экспорт/импорт настроек

### Версия 2.2 (планируется)

- [ ] Интеграция с Midjourney
- [ ] Video-to-3D модуль
- [ ] AI Animation Assistant
- [ ] Облачная синхронизация истории

### Версия 3.0 (будущее)

- [ ] Локальные AI модели (без интернета)
- [ ] Обучение кастомных моделей
- [ ] Плагин-система для сообщества
- [ ] Marketplace для провайдеров

---

## 📊 Статистика проекта

- **Строк кода:** ~15,000+
- **Модулей:** 50+
- **Провайдеров:** 4 (встроенных)
- **Операторов:** 13
- **Панелей:** 6

---

## ⭐ Если вам нравится проект

Поставьте звезду на GitHub и поделитесь с друзьями!

---

**Версия:** 2.0.0  
**Дата релиза:** 2024  
**Статус:** Stable

**Создано с ❤️ для сообщества Blender**
