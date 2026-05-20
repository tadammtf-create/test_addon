import bpy

class AIToolkitProperties(bpy.types.PropertyGroup):
    prompt: bpy.props.StringProperty(
        name="Name",
        default="MyModel"
    )
    image_path: bpy.props.StringProperty(
        name="Image Path",
        subtype='FILE_PATH'
    )
    #  Выбор сервера для Hunyuan
    space_id: bpy.props.EnumProperty(
        name="HuggingFace Space",
        items=[
            ("Jbowyer/Hunyuan3D-2.1", "Hunyuan3D-2.1 (L40S)", "Running on L40S GPU"),
            # Можно добавить другие сервера, если нужно
        ],
        default="Jbowyer/Hunyuan3D-2.1"
    )

    # Свойства для генерации изображений
    image_prompt: bpy.props.StringProperty(
        name="Image Prompt",
        default="A robotic cyberpunk fish swimming through a neon city, cinematic, 8k",
        description="Prompt for image generation"
    )
    hf_token: bpy.props.StringProperty(
        name="HuggingFace Token",
        default="",
        description="Your HuggingFace token from https://huggingface.co",
        subtype='PASSWORD'
    )
    image_width: bpy.props.IntProperty(
        name="Width",
        default=1024,
        min=512,
        max=2048
    )
    image_height: bpy.props.IntProperty(
        name="Height",
        default=1024,
        min=512,
        max=2048
    )

    # Статус генерации изображения
    generation_status: bpy.props.StringProperty(
        name="Status",
        default="Ready",
        description="Current generation status"
    )

    # Выбор API для генерации изображений
    image_api_type: bpy.props.EnumProperty(
        name="API Type",
        items=[
            ("flux_1", "API v1 (Original)", "Original FLUX API implementation"),
            ("flux_2", "API v2 (New)", "New FLUX API implementation"),
        ],
        default="flux_2",
        description="Choose which API to use for image generation"
    )
