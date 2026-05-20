import os
import time
import shutil
from gradio_client import Client, handle_file
from huggingface_hub import login


REPO_ID = "black-forest-labs/FLUX.1-dev"

def find_image(data):
    """Рекурсивный поиск пути к изображению в ответе API"""
    if isinstance(data, str):
        if any(data.lower().endswith(ext) for ext in [".webp", ".png", ".jpg", ".jpeg"]):
            return data
    if isinstance(data, (list, tuple)):
        for item in data:
            found = find_image(item)
            if found: return found
    if isinstance(data, dict):
        
        if 'path' in data: return data['path']
        for v in data.values():
            found = find_image(v)
            if found: return found
    
    if hasattr(data, 'path'):
        return data.path
    return None

def generate_image(prompt, hf_token, width=1024, height=1024, callback=None):
    try:
        if not hf_token:
            if callback: callback("Ошибка: Токен HF не указан")
            return None

        # 1. Авторизация
        login(token=hf_token, add_to_git_credential=False)
        
        if callback: callback(f"🔌 Подключение к {REPO_ID}...")
        client = Client(REPO_ID)

        if callback: callback("🎨 Отправка запроса на генерацию...")

        # 2. Вызов основного метода /infer

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

        # 3. Ожидание результата
        while not job.done():
            status = job.status()
            if callback:
                # Получаем rank в очереди, если он есть
                msg = f"⏳ Статус: {status.code}"
                if hasattr(status, 'rank') and status.rank is not None:
                    msg += f" (Очередь: {status.rank})"
                callback(msg)
            time.sleep(1)

        result = job.result()

        # Отладочный вывод структуры ответа
        print(f"[TWO_API DEBUG] Тип результата: {type(result)}")
        print(f"[TWO_API DEBUG] Содержимое: {result}")

        # Если это кортеж или список, выводим каждый элемент
        if isinstance(result, (tuple, list)):
            for i, item in enumerate(result):
                print(f"[TWO_API DEBUG] Элемент [{i}]: тип={type(item)}, значение={item}")
                if hasattr(item, '__dict__'):
                    print(f"[TWO_API DEBUG] Атрибуты элемента [{i}]: {item.__dict__}")

        # 4. Поиск и сохранение файла
        img_temp_path = find_image(result)
        
        if not img_temp_path or not os.path.exists(img_temp_path):
            if callback: callback("❌ Файл не найден в ответе API")
            return None

        # Путь к рабочему столу (Windows)
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        filename = f"flux_{int(time.time())}.webp"
        final_dest = os.path.join(desktop, filename)

        shutil.copy(img_temp_path, final_dest)

        if callback: callback(f"✅ Готово! Сохранено: {filename}")
        return final_dest

    except Exception as e:
        if callback: callback(f"💥 Ошибка: {str(e)}")
        print(f"Детали ошибки: {e}")
        return None

# Пример запуска:
# res = generate_image("cyberpunk cat with neon lights", "ваш_hf_token", callback=print)
