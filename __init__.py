bl_info = {
    "name": "AI Toolkit",
    "author": "Nelly",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > AI Toolkit",
    "description": "AI Toolkit Platform — Text-to-3D, Image-to-3D, AI Texturing, Render Preview, AI Assistant",
    "category": "3D View",
}

import bpy
import subprocess
import sys


# --- АВТО-УСТАНОВКА GRADIO ---
def install_dependencies():
    """Best-effort install of the gradio client used by the legacy pipeline.

    Wrapped so a failed install (no network, sandboxed Blender) never
    aborts addon registration — the new UI is pure ``bpy`` and works
    without the dependency.
    """
    try:
        import gradio_client  # noqa: F401
    except ImportError:
        print("AI Toolkit: Installing gradio_client...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "gradio_client", "--user"]
            )
        except Exception as exc:  # noqa: BLE001 — never abort register
            print(f"AI Toolkit: gradio_client install skipped: {exc}")


install_dependencies()


# ---------------------------------------------------------------------------
# Legacy operators — still registered so older keybindings / external scripts
# that call ``bpy.ops.aitoolkit.*`` keep working. The new UI does NOT depend
# on them; it uses the ``aitk.*`` operator family from ``ai_toolkit_ui``.
# ---------------------------------------------------------------------------
from .properties import AIToolkitProperties
from .operators import (
    AIToolkitGenerate,
    AIToolkitOpenManual,
    AIToolkitGenerateImage,
)

# New Blender-native UI package.
from . import ai_toolkit_ui


_legacy_classes = (
    AIToolkitProperties,
    AIToolkitGenerate,
    AIToolkitOpenManual,
    AIToolkitGenerateImage,
)


def register():
    """Register legacy operator classes + the new UI package.

    The floating-launcher overlay (``ui.launcher_overlay``) from the
    earlier design is intentionally NOT registered — the new design
    delivers the addon through a standard sidebar panel instead, per
    the Blender-native UI redesign.
    """
    for cls in _legacy_classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)

    # Legacy Scene pointer — still attached so the old operators that
    # read ``context.scene.ai_toolkit`` keep working until they are
    # replaced.
    bpy.types.Scene.ai_toolkit = bpy.props.PointerProperty(type=AIToolkitProperties)

    # New UI package owns its own pointer-property (``Scene.aitk``)
    # and registers every PropertyGroup / Operator / Panel it needs.
    ai_toolkit_ui.register()


def unregister():
    """Reverse of :func:`register`."""
    try:
        ai_toolkit_ui.unregister()
    except Exception:  # noqa: BLE001 — never abort unregister
        pass

    if hasattr(bpy.types.Scene, "ai_toolkit"):
        try:
            del bpy.types.Scene.ai_toolkit
        except Exception:  # noqa: BLE001
            pass

    for cls in reversed(_legacy_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    register()
