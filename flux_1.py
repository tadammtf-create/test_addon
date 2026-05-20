import os
import time
import shutil
from gradio_client import Client
from huggingface_hub import login

REPO_ID = "black-forest-labs/FLUX.1-dev"

def find_image(data):
    """Рекурсивный поиск пути к изображению в ответе API"""
    # Проверяем строки с расширениями изображений
    if isinstance(data, str):
        if any(data.endswith(ext) for ext in [".webp", ".png", ".jpg", ".jpeg", ".gif"]):
            return data
        # Иногда путь может быть в формате file://
        if data.startswith("file://"):
            return data.replace("file://", "")

    # Проверяем списки и кортежи
    if isinstance(data, (list, tuple)):
        for item in data:
            found = find_image(item)
            if found:
                return found

    # Проверяем словари
    if isinstance(data, dict):
        # Сначала проверяем ключи, которые обычно содержат путь к файлу
        for key in ['path', 'url', 'file', 'image', 'output']:
            if key in data:
                found = find_image(data[key])
                if found:
                    return found
        # Затем проверяем все остальные значения
        for v in data.values():
            found = find_image(v)
            if found:
                return found

    # Проверяем объекты с атрибутом path
    if hasattr(data, 'path'):
        return find_image(data.path)

    return None

def generate_image(prompt, hf_token, width=1024, height=1024, callback=None):
    """
    Генерирует изображение через FLUX API

    Args:
        prompt: текстовое описание изображения
        hf_token: HuggingFace токен
        width: ширина изображения
        height: высота изображения
        callback: функция для обновления статуса (опционально)

    Returns:
        str: путь к сохраненному файлу на рабочем столе или None при ошибке
    """
    try:
        if not hf_token or hf_token.strip() == "":
            if callback:
                callback("Ошибка: HuggingFace токен не указан")
            return None

        if callback:
            callback("🔐 Авторизация в HuggingFace...")

        # Авторизация через huggingface_hub
        login(token=hf_token, add_to_git_credential=False)

        if callback:
            callback(f"🔌 Подключаюсь к {REPO_ID}...")

        client = Client(REPO_ID)

        if callback:
            callback("📤 Отправляю задание на генерацию...")

        # Отправка задания
        job = client.submit(
            prompt=prompt,
            seed=0,
            randomize_seed=True,
            width=width,
            height=height,
            guidance_scale=3.5,
            num_inference_steps=28,
            api_name="/infer"
        )

        # Мониторинг статуса
        while not job.done():
            status = job.status()
            if callback:
                if status.queue_size and status.queue_size > 0:
                    callback(f"⏳ В очереди: {status.rank}/{status.queue_size}")
                else:
                    callback(f"🎨 Генерация... ({status.code})")
            time.sleep(2)

        if callback:
            callback("💾 Генерация завершена. Сохраняю изображение...")

        # Получение результата
        result = job.result()

        if callback:
            callback(f"Результат API: {type(result)}")

        # Детальный вывод структуры ответа для отладки
        print(f"[FLUX DEBUG] Полный ответ API:")
        print(f"[FLUX DEBUG] Тип: {type(result)}")
        print(f"[FLUX DEBUG] Содержимое: {result}")

        img_temp_path = find_image(result)

        if img_temp_path:
            print(f"[FLUX DEBUG] Найден путь к изображению: {img_temp_path}")

        if not img_temp_path:
            if callback:
                callback(f"Ошибка: путь к файлу не найден. Ответ: {result}")
            return None

        # Проверяем существование временного файла
        if not os.path.exists(img_temp_path):
            if callback:
                callback(f"Ошибка: временный файл не существует: {img_temp_path}")
            return None

        # Сохранение на Рабочий стол
        desktop = os.path.join(os.path.join(os.environ['USERPROFILE']), 'Desktop')
        filename = f"flux_generated_{int(time.time())}.webp"
        final_dest = os.path.join(desktop, filename)

        shutil.copy(img_temp_path, final_dest)

        if callback:
            callback(f"✅ Готово! Файл: {filename}")

        return final_dest

    except Exception as e:
        error_msg = f"Ошибка: {type(e).__name__}: {str(e)}"
        if callback:
            callback(error_msg)
        print(f"[FLUX ERROR DETAILS] {error_msg}")
        import traceback
        traceback.print_exc()
        return None
