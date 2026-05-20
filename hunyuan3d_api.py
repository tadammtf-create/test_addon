import os
import time
import shutil
import bpy
from gradio_client import Client, handle_file

# Добавили аргумент space_id
def run_3d_logic(input_image, prompt_name, space_id):
    try:
        print(f"Подключение к Space: {space_id}")
        client = Client(space_id, httpx_kwargs={"timeout": 300.0})
        
        job = client.submit(
            image=handle_file(input_image),
            mv_image_front=None, mv_image_back=None, mv_image_left=None, mv_image_right=None,
            steps=30, guidance_scale=5.0, seed=1234, octree_resolution=256,
            check_box_rembg=True, num_chunks=8000, randomize_seed=True,
            api_name="/generation_all"
        )

        while not job.done():
            time.sleep(2)

        result = job.result()
        
        def find_glb(data):
            if isinstance(data, str) and data.endswith(".glb"): return data
            if isinstance(data, (list, tuple)):
                for item in data:
                    found = find_glb(item)
                    if found: return found
            if isinstance(data, dict):
                for v in data.values():
                    found = find_glb(v)
                    if found: return found
            return None

        model_path = find_glb(result)
        
        if model_path:
            bpy.ops.import_scene.gltf(filepath=model_path)
            return True
        return False

    except Exception as e:
        print(f"Ошибка API: {e}")
        return False
