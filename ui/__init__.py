"""AI Toolkit UI layer package.

All Blender-facing code (panels, operators, draw handlers, modal operators,
preferences) lives under this package. Modules here are free to import
``bpy`` and other Blender-distributed modules.

Registration order
------------------
``register()`` performs the following steps, in order, so the floating
launcher icon and its click router are wired up the moment the addon
finishes registering:

1. Build a :class:`SettingsStore` and bind it into both the overlay
   draw handler (``launcher_overlay.bind_settings``) and the click
   router operator (``launcher_ops.bind_settings``) BEFORE any handler
   or operator that reads it is registered. This is what guarantees
   ``_settings`` is non-``None`` on the first redraw / first click.
2. Register the launcher operator classes
   (``AITK_OT_launcher_click_router``, ``AITK_OT_launcher_menu``,
   ``AITK_OT_open_documentation``, ``AITK_OT_open_preferences``) so
   ``bpy.ops.aitk.launcher_menu`` and friends are callable.
3. Register the overlay draw handler so the floating button is
   painted on every viewport redraw.
4. Add the ``LEFTMOUSE`` PRESS keymap entry in the addon keyconfig's
   ``3D View`` keymap. The click router's ``invoke`` hit-tests the
   cursor against the launcher rectangle and either dispatches to
   ``aitk.launcher_menu`` (on hit) or returns ``PASS_THROUGH`` (on
   miss) so unrelated viewport input is unaffected.

``unregister()`` reverses these steps. Keymap items are tracked in a
module-level list so the exact items added by ``register()`` are the
ones removed; this avoids ``keymap_items.new`` accumulating duplicate
entries across addon reloads.
"""

import logging

import bpy

from . import panels
from . import launcher_overlay
from . import launcher_menu
from .operators import launcher_ops

logger = logging.getLogger("ai_toolkit")


# Operator classes registered with Blender. Order matters for register
# (the launcher_menu must be registered before the click router calls
# bpy.ops.aitk.launcher_menu); reverse order is used for unregister.
_OPERATOR_CLASSES = (
    launcher_menu.AITK_OT_launcher_menu,
    launcher_ops.AITK_OT_launcher_click_router,
    launcher_ops.AITK_OT_open_documentation,
    launcher_ops.AITK_OT_open_preferences,
)


# Keymap entries added by ``register()``. Each entry is a
# ``(KeyMap, KeyMapItem)`` pair so ``unregister()`` can remove the exact
# item it added without searching by id/name.
_keymap_items: list = []


def _register_keymaps() -> None:
    """Bind ``LEFTMOUSE`` PRESS in the 3D View keymap to the click router.

    Uses ``wm.keyconfigs.addon`` so the entry lives in the addon
    keyconfig (the standard place for addon-owned keymap items in
    Blender 4.x). Headless / background Blender does not always expose
    an addon keyconfig — in that case we log a warning and return so
    addon registration still completes cleanly.

    Idempotent: if a previous ``register()`` already added an entry
    (``_keymap_items`` non-empty) this is a no-op, mirroring the
    overlay handler's idempotency contract.
    """
    if _keymap_items:
        return

    wm = bpy.context.window_manager
    kc = getattr(wm, "keyconfigs", None)
    kc_addon = getattr(kc, "addon", None) if kc is not None else None
    if kc_addon is None:
        logger.warning(
            "No addon keyconfig available (headless / background Blender?); "
            "the floating launcher will draw but clicks will not be routed."
        )
        return

    km = kc_addon.keymaps.new(name="3D View", space_type="VIEW_3D")
    kmi = km.keymap_items.new(
        "aitk.launcher_click_router", "LEFTMOUSE", "PRESS",
    )
    _keymap_items.append((km, kmi))


def _unregister_keymaps() -> None:
    """Remove exactly the keymap items added by ``_register_keymaps()``.

    Tolerant of items Blender has already torn down during shutdown
    (raises ``RuntimeError`` or ``ReferenceError``); the failure is
    logged at debug level and teardown continues so a partially
    invalid state never blocks the addon's ``unregister()``.
    """
    for km, kmi in _keymap_items:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError) as exc:
            logger.debug(
                "Failed to remove launcher keymap item (%s: %s); "
                "Blender likely removed it already.",
                type(exc).__name__,
                exc,
            )
    _keymap_items.clear()


def register():
    # Bind settings BEFORE the click router class is registered so its
    # poll() never observes a None _settings on the first click after
    # register.
    try:
        from ..services.settings.store import SettingsStore
        settings_store = SettingsStore()
        launcher_overlay.bind_settings(settings_store)
        launcher_ops.bind_settings(settings_store)
    except Exception as e:  # noqa: BLE001 -- never abort addon register
        logger.warning(
            "Failed to bind SettingsStore to launcher overlay (%s: %s); "
            "the floating launcher will be hidden until the next reload.",
            type(e).__name__,
            e,
        )

    # Register operator classes used by the launcher click path. Each
    # registration is guarded individually so a stale class left over
    # from a previous reload does not block the rest.
    for cls in _OPERATOR_CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to register %s (%s: %s); "
                "the operator may already be registered from a prior reload.",
                cls.__name__,
                type(exc).__name__,
                exc,
            )

    # Регистрируем панели интерфейса
    if hasattr(panels, "register"):
        panels.register()

    # Регистрируем плавающий лаунчер (иконку)
    if hasattr(launcher_overlay, "register"):
        launcher_overlay.register()

    # Wire LEFTMOUSE → click router. Must happen AFTER the click router
    # class is registered (otherwise the keymap item references an
    # unknown operator id).
    _register_keymaps()


def unregister():
    # Reverse of register(): keymap → overlay → panels → operator classes.
    _unregister_keymaps()

    if hasattr(launcher_overlay, "unregister"):
        launcher_overlay.unregister()
    if hasattr(panels, "unregister"):
        panels.unregister()

    for cls in reversed(_OPERATOR_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError) as exc:
            logger.debug(
                "Failed to unregister %s (%s: %s); already gone?",
                cls.__name__,
                type(exc).__name__,
                exc,
            )
