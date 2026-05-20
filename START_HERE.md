# 🚀 НАЧНИТЕ ЗДЕСЬ

## Добро пожаловать в AI Toolkit Platform!

Это краткое руководство поможет вам быстро начать работу.

---

## ⚡ Самый быстрый путь (3 минуты)

### 1. Упакуйте аддон

```bash
python package_addon.py
```

**Результат:** Файл `dist/ai_toolkit.zip` готов!

### 2. Установите в Blender

1. Откройте **Blender 4.0+**
2. **Edit** → **Preferences** → **Add-ons** → **Install...**
3. Выберите `dist/ai_toolkit.zip`
4. Поставьте **галочку** ✅
5. **Save Preferences**

### 3. Настройте

1. Разверните аддон в списке
2. Введите **HuggingFace Token**: https://huggingface.co/settings/tokens
3. Введите **OpenAI API Key**: https://platform.openai.com/api-keys
4. Нажмите **Test Connection** для каждого

### 4. Готово! 🎉

Откройте **3D Viewport** → Кнопка **AI Toolkit** в левом нижнем углу

---

## 📚 Какой документ читать?

### Я новичок, хочу быстро установить
→ **[QUICK_START.md](QUICK_START.md)** (5 минут)

### Мне нужна подробная инструкция
→ **[INSTALLATION.md](INSTALLATION.md)** (полное руководство)

### Я визуал, хочу схему процесса
→ **[INSTALLATION_FLOWCHART.md](INSTALLATION_FLOWCHART.md)** (блок-схема)

### Я опытный пользователь, нужна справка
→ **[PACKAGING_SUMMARY.md](PACKAGING_SUMMARY.md)** (краткое резюме)

### Я разработчик, готовлю релиз
→ **[PRE_RELEASE_CHECKLIST.md](PRE_RELEASE_CHECKLIST.md)** (100+ пунктов)

### Хочу понять проект в целом
→ **[README.md](README.md)** (обзор проекта)

### Не знаю, что мне нужно
→ **[DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md)** (индекс всех документов)

---

## 🎯 Что дальше?

После установки:

1. **Протестируйте Text-to-3D:**
   - Launcher → Text-to-3D
   - Промпт: "a simple cube"
   - Generate!

2. **Попробуйте AI Assistant:**
   - Launcher → AI Assistant
   - Включите "Include scene context"
   - Спросите: "What objects are in my scene?"

3. **Изучите другие модули:**
   - Image-to-3D
   - AI Texturing
   - Render Preview

4. **Проверьте History:**
   - Launcher → History
   - Все задачи сохраняются здесь

---

## 🐛 Проблемы?

| Проблема | Решение |
|----------|---------|
| Аддон не появляется | Проверьте `bl_info` в `__init__.py` |
| Ошибка при активации | System Console (Window → Toggle System Console) |
| Кнопка не видна | Preferences → Enable Launcher Overlay ✅ |
| "No provider" | Введите учетные данные + Test Connection |
| Задачи зависают | Перезапустите Blender |

**Подробнее:** [INSTALLATION.md](INSTALLATION.md) → Раздел 6

---

## 📦 Структура проекта

```
ai_toolkit/                    # ← Это ваш аддон
├── __init__.py               # Точка входа (ОБЯЗАТЕЛЬНО с bl_info!)
├── ui/                       # UI слой (с bpy)
│   ├── operators/
│   │   └── job_ops.py       # ✅ ВСЕ операторы реализованы!
│   └── panels/              # Панели модулей
└── services/                 # Service слой (без bpy)
    ├── jobs/                # Система задач
    ├── providers/           # AI провайдеры
    └── ...
```

---

## ✅ Статус реализации

### Полностью готово:

- ✅ **Service Layer** (jobs, providers, settings, history, scene)
- ✅ **UI Layer** (launcher, panels, operators, dispatcher, importer)
- ✅ **Операторы** (все 13 операторов в `job_ops.py`)
- ✅ **Провайдеры** (FLUX v1/v2, Hunyuan3D, OpenAI Chat)
- ✅ **Документация** (9 документов на русском)

### Готово к использованию! 🎉

---

## 🔑 Ключевые файлы

| Файл | Статус | Описание |
|------|--------|----------|
| `ui/operators/job_ops.py` | ✅ Готов | Все операторы (16.2-20.3) |
| `__init__.py` | ⚠️ Проверьте | Должен содержать `bl_info` |
| `services/providers/*.py` | ✅ Готовы | 4 провайдера |
| `ui/panels/*.py` | ✅ Готовы | 6 панелей |
| Документация | ✅ Готова | 9 файлов |

---

## 🎓 Полезные команды

```bash
# Упаковка аддона
python package_addon.py

# Очистка кэша (Windows PowerShell)
Get-ChildItem -Path "ai_toolkit" -Recurse -Include "__pycache__","*.pyc" | Remove-Item -Recurse -Force

# Проверка структуры ZIP
# Откройте ai_toolkit.zip и убедитесь:
# - Первый уровень: ai_toolkit/
# - Внутри: __init__.py
```

---

## 📞 Нужна помощь?

1. **Проверьте документацию:**
   - [QUICK_START.md](QUICK_START.md) — Быстрый старт
   - [INSTALLATION.md](INSTALLATION.md) — Подробная инструкция
   - [README.md](README.md) — Обзор проекта

2. **Проверьте консоль:**
   - Window → Toggle System Console
   - Ищите ошибки (красный текст)

3. **Включите DEBUG-логи:**
   ```python
   import logging
   logging.getLogger("ai_toolkit").setLevel(logging.DEBUG)
   ```

4. **Создайте issue:**
   - GitHub Issues (если настроен)
   - Приложите логи из консоли

---

## 🎯 Контрольный список

Перед использованием убедитесь:

- [ ] `ai_toolkit/__init__.py` содержит `bl_info`
- [ ] Все папки имеют `__init__.py`
- [ ] Удалены `__pycache__` и `.pyc`
- [ ] ZIP создан с правильной структурой
- [ ] Аддон установлен и активирован
- [ ] Настройки сохранены (Save Preferences)
- [ ] Учетные данные введены
- [ ] Test Connection успешен
- [ ] Launcher отображается в viewport
- [ ] Тестовая задача выполнена

**Всё готово? Начинайте творить! 🚀**

---

## 📊 Быстрая справка

### Модули:

1. **Text-to-3D** — Текст → 3D модель
2. **Image-to-3D** — Изображение → 3D модель
3. **AI Texturing** — Генерация текстур для объекта
4. **Render Preview** — AI-превью рендера
5. **AI Assistant** — Чат с анализом сцены

### Провайдеры:

- **FLUX** — Text-to-Image (HuggingFace)
- **Hunyuan3D** — Image-to-3D
- **OpenAI** — Chat (AI Assistant)

### Горячие клавиши:

- `N` — Открыть N-panel (альтернативный доступ)
- `Ctrl+Alt+U` — Preferences
- Launcher — Клик на кнопку в viewport

---

## 🌟 Особенности

- ✨ **Floating Launcher** — Кнопка прямо в viewport
- 🔄 **Многопоточность** — До 4 задач одновременно
- 📜 **История** — Все результаты сохраняются
- 🎨 **Темы** — Автоматическая подстройка под Blender
- 🔌 **Модульность** — Легко добавлять провайдеров

---

## 📈 Следующие шаги

1. ✅ Установите аддон (следуйте инструкции выше)
2. ✅ Протестируйте каждый модуль
3. ✅ Изучите настройки
4. ✅ Прочитайте [README.md](README.md) для деталей
5. ✅ Начните создавать с AI!

---

## 💡 Совет

**Сохраните этот файл!** Он содержит все ссылки на документацию и быстрые команды.

---

**Версия:** 1.0  
**Дата:** 2024  
**Статус:** ✅ Готово к использованию

**Удачи! 🎉**
