import bpy
import os
from .hunyuan3d_api import run_3d_logic
from . import flux_1
from . import flux_2

class AIToolkitGenerate(bpy.types.Operator):
    bl_idname = "aitoolkit.generate"
    bl_label = "GENERATE"
    bl_description = "Run Hunyuan 3D generation via API"

    def execute(self, context):
        props = context.scene.ai_toolkit
        
        # Превращаем относительный путь Blender (//) в полный системный путь
        image_path = bpy.path.abspath(props.image_path)
        
        # Проверяем, выбран ли файл и существует ли он
        if not image_path or not os.path.exists(image_path):
            self.report({'ERROR'}, f"Файл изображения не найден: {image_path}")
            return {'CANCELLED'}
        
        self.report({'INFO'}, "Connecting to AI... Blender will freeze for a bit.")
        
        # Вызываем логику из hunyuan3d_api.py
        try:
            # Передаем путь к картинке и имя модели (промпт)
            success = run_3d_logic(image_path, props.prompt, props.space_id)
            if success:
                self.report({'INFO'}, "Model imported successfully!")
            else:
                self.report({'ERROR'}, "Generation failed or GLB not found.")
        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
            
        return {'FINISHED'}

class AIToolkitOpenManual(bpy.types.Operator):
    bl_idname = "aitoolkit.open_manual"
    bl_label = "Open Manual"

    def execute(self, context):
        # Путь к локальному сайту аддона
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        manual_path = os.path.join(addon_dir, "manual_addon", "index.html")
        bpy.ops.wm.url_open(url=f"file:///{manual_path}")
        return {'FINISHED'}

class AIToolkitGenerateImage(bpy.types.Operator):
    bl_idname = "aitoolkit.generate_image"
    bl_label = "Generate Image"
    bl_description = "Generate image using FLUX API (async, non-blocking)"

    _timer = None
    _thread = None
    _result_path = None
    _error_message = None
    _is_running = False
    _current_status = "Ready"

    def modal(self, context, event):
        props = context.scene.ai_toolkit

        if event.type == 'TIMER':
            # Обновляем статус в UI
            props.generation_status = self._current_status

            # Принудительно обновляем UI
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

            # Проверяем, завершилась ли генерация
            if not self._is_running:
                self.cancel(context)
                if self._result_path:
                    props.generation_status = "✓ Image saved to Desktop!"
                    self.report({'INFO'}, f"Image saved to Desktop!")
                elif self._error_message:
                    props.generation_status = f"✗ Error: {self._error_message}"
                    self.report({'ERROR'}, f"Failed: {self._error_message}")
                else:
                    props.generation_status = "✗ Generation failed"
                    self.report({'ERROR'}, "Image generation failed")
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        props = context.scene.ai_toolkit

        # Проверка токена
        if not props.hf_token:
            self.report({'ERROR'}, "Please enter your HuggingFace token in settings")
            return {'CANCELLED'}

        # Проверка промпта
        if not props.image_prompt:
            self.report({'ERROR'}, "Please enter a prompt")
            return {'CANCELLED'}

        self._is_running = True
        self._result_path = None
        self._error_message = None
        self._current_status = "Starting..."
        props.generation_status = "Starting..."

        # Запускаем таймер для обновления UI
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)

        # Запускаем генерацию в отдельном потоке
        import threading

        def run_generation():
            try:
                def status_callback(message):
                    print(f"[FLUX] {message}")
                    self._current_status = message

                # Выбираем API в зависимости от настройки
                if props.image_api_type == "flux_2":
                    result = flux_2.generate_image(
                        prompt=props.image_prompt,
                        hf_token=props.hf_token,
                        width=props.image_width,
                        height=props.image_height,
                        callback=status_callback
                    )
                else:  # flux_1
                    result = flux_1.generate_image(
                        prompt=props.image_prompt,
                        hf_token=props.hf_token,
                        width=props.image_width,
                        height=props.image_height,
                        callback=status_callback
                    )

                self._result_path = result
                if not result:
                    self._error_message = "No image path returned from API"
            except Exception as e:
                self._error_message = str(e)
                print(f"[FLUX ERROR] {e}")
            finally:
                self._is_running = False

        self._thread = threading.Thread(target=run_generation)
        self._thread.start()

        self.report({'INFO'}, "Image generation started")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
