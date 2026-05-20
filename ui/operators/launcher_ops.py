"""Operators for the AI Toolkit Floating Launcher.

Click routing for the overlay button (task 12.2), Documentation /
Settings dispatch operators (task 13.2). The Test Connection operator
(task 23.2) is appended to this file by its respective task.

Layer
-----
This module is part of the **UI Layer** and imports :mod:`bpy` at module
top level. The hit-test geometry it consults
(:func:`~ai_toolkit.services.validation.hit_test`) and the
:class:`~ai_toolkit.services.settings.store.SettingsStore` it reads from
are bpy-free service-layer components, so this file is the only place
where the launcher click path crosses the layer boundary
(Requirement 13.1).

Settings binding
----------------
The operator consults :data:`_settings`, a module-level
:class:`SettingsStore` reference bound by the addon's top-level
``__init__.register()`` (task 24.1) via :func:`bind_settings` immediately
after the store is constructed and before any UI class is registered.
Routing every click through ``bpy.context.preferences.addons[...]``
instead would add a per-event lookup hop and make this file harder to
unit-test without Blender running; binding once at register time keeps
both costs at zero.

Keymap binding
--------------
This module defines only the operator class. The ``LEFTMOUSE`` keymap
entry that wires :class:`AITK_OT_launcher_click_router` to the
``VIEW_3D`` ``Window`` keymap is added by the addon's top-level
``__init__.register()`` (task 24.1) and removed by the matching
``unregister()``. That task owns the keymap lifetime so the entry
survives the addon being toggled and never leaks across reloads.
"""

from __future__ import annotations

import logging
import webbrowser
from typing import Optional

import bpy

from ...services.settings.schema import DOCUMENTATION_URL, LAUNCHER_OVERLAY_ENABLED
from ...services.settings.store import SettingsStore
from ...services.validation import hit_test, is_valid_url
from ..launcher_overlay import get_launcher_rect


# Single addon-wide logger; same handle the service layer uses.
logger = logging.getLogger("ai_toolkit")


# Package name as it appears in :attr:`bpy.context.preferences.addons`.
# The addon is registered under ``ai_toolkit`` by the top-level
# ``__init__.py`` (task 24.1); :class:`AITK_OT_open_preferences` uses
# this constant to ask Blender's
# :mod:`bpy.ops.preferences.addon_show` operator to scroll to and
# expand this addon's row in the preferences window. Declared as a
# module-level constant so the operator does not need a circular
# import back to ``ai_toolkit/__init__.py`` to discover the name.
ADDON_PACKAGE_NAME = "ai_toolkit"


# Module-level reference to the SettingsStore. Bound by addon
# ``__init__.register()`` so the operator can read the
# overlay-enabled flag without a roundtrip through bpy preferences for
# every click. ``None`` between module import and the bind call (and
# again after a hot reload before re-bind), and
# :meth:`AITK_OT_launcher_click_router.poll` treats that state as
# "not yet ready" so a stray invocation cannot crash.
_settings: Optional[SettingsStore] = None


def bind_settings(settings: SettingsStore) -> None:
    """Bind the :class:`SettingsStore` the launcher operators consult.

    Called by the addon's top-level ``__init__.register()`` (task 24.1)
    once the store has been constructed and seeded from
    :class:`bpy.types.AddonPreferences`. Idempotent: a subsequent call
    replaces the reference, which is the desired behaviour when Blender
    reloads the addon and the previous store instance is discarded.
    """
    global _settings
    _settings = settings


class AITK_OT_launcher_click_router(bpy.types.Operator):
    """Routes ``LEFTMOUSE`` clicks in ``VIEW_3D`` to the launcher menu.

    Bound to ``LEFTMOUSE`` in the ``3D View`` keymap by the addon's
    top-level ``__init__.register()`` (task 24.1). On every left-click
    in a 3D Viewport, Blender invokes this operator first; it
    hit-tests the cursor against the launcher rect computed by
    :func:`get_launcher_rect`. A click inside the rect is consumed and
    dispatched to ``AITK_OT_launcher_menu``; a click outside falls
    through to whatever handler would have received the event next, so
    object selection and other viewport interactions are unaffected
    (Requirements 1.7, 2.9).

    The operator carries ``bl_options = {"INTERNAL"}`` so it does not
    appear in Blender's ``F3`` operator search or the user's spacebar
    menu — it is wiring, not a user-facing command.
    """

    bl_idname = "aitk.launcher_click_router"
    bl_label = "AI Toolkit Launcher Click Router"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        """Return ``True`` iff the launcher is enabled in a 3D Viewport.

        Four gates, in cheapest-first order:

        1. :data:`_settings` must be bound. Between module import and
           the addon's top-level ``__init__.register()`` call to
           :func:`bind_settings`, the store reference is ``None`` and
           the launcher cannot be enabled at all (Requirements 1.7,
           1.9).
        2. The
           :data:`~ai_toolkit.services.settings.schema.LAUNCHER_OVERLAY_ENABLED`
           setting must be truthy. When the user has disabled the
           overlay, the launcher is hidden and clicks must pass
           through unmodified (Requirement 1.9 and the fallback
           sidebar contract in Requirement 1.11).
        3. ``context.area`` must exist. The keymap is registered
           against ``VIEW_3D`` so this should always hold for invoked
           events, but Blender occasionally polls operators from
           contexts without an area (the spacebar menu, the F3 search,
           script-driven invocation) and the defensive check costs
           nothing.
        4. ``context.area.type`` must be exactly ``"VIEW_3D"`` so the
           launcher never fires from a Properties or Outliner area
           that happens to share the keymap (Requirement 1.8).
        """
        if _settings is None:
            return False
        if not _settings.get(LAUNCHER_OVERLAY_ENABLED):
            return False
        area = context.area
        if area is None or area.type != "VIEW_3D":
            return False
        return True

    def invoke(self, context, event):
        """Hit-test the click; dispatch to the launcher menu or pass through.

        Reads ``event.mouse_region_x`` / ``event.mouse_region_y``,
        which Blender provides in region-local pixel coordinates with
        the origin at the bottom-left of the active region — the same
        coordinate system that
        :func:`~ai_toolkit.ui.launcher_overlay.get_launcher_rect`
        produces and that
        :func:`~ai_toolkit.services.validation.hit_test` expects. This
        keeps the geometry path uniform across the launcher draw
        handler, the click router, and the hit-test property test
        (Property 2).

        On a hit (Requirements 1.7, 2.9):

        * Dispatches to ``AITK_OT_launcher_menu`` via
          ``bpy.ops.aitk.launcher_menu('INVOKE_DEFAULT')``. The
          ``INVOKE_DEFAULT`` execution context is required so the menu
          operator's own ``invoke`` is run (it needs the cursor
          position from the live event), rather than the bare
          ``execute``.
        * Wraps the dispatch in a try/except: on any exception the
          failure is logged with a stack trace, the user sees an
          ``{'ERROR'}`` report, and the operator returns
          ``{'CANCELLED'}`` so Blender treats the event as handled and
          does not propagate it. Catching ``Exception`` rather than a
          narrower subclass is intentional — Blender's operator
          dispatch can raise ``RuntimeError``, ``AttributeError`` (when
          the menu operator is not yet registered during a hot
          reload), and Blender-specific errors during shutdown.
        * On success returns ``{'FINISHED'}`` so the click is consumed
          and does not also trigger object selection underneath the
          launcher button.

        On a miss the operator returns ``{'PASS_THROUGH'}`` so any
        handler that would have received the click — object
        selection, gizmo manipulation, other addons — still does.
        """
        rect = get_launcher_rect(_settings)
        point = (event.mouse_region_x, event.mouse_region_y)
        if hit_test(rect, point):
            try:
                bpy.ops.aitk.launcher_menu("INVOKE_DEFAULT")
            except Exception:
                logger.exception(
                    "Launcher click router: failed to open launcher menu"
                )
                self.report({"ERROR"}, "Failed to open launcher menu")
                return {"CANCELLED"}
            return {"FINISHED"}
        return {"PASS_THROUGH"}


class AITK_OT_open_documentation(bpy.types.Operator):
    """Open the configured documentation URL in the user's browser.

    Wired to the "Documentation" entry of the Launcher_Menu and to the
    "Documentation" button in the addon preferences window. The URL is
    read from :data:`_settings` (bound by the addon's top-level
    ``__init__.register()`` via :func:`bind_settings`) under the
    :data:`~ai_toolkit.services.settings.schema.DOCUMENTATION_URL`
    key, validated with
    :func:`~ai_toolkit.services.validation.is_valid_url`, and only
    then handed to :func:`webbrowser.open` from the Python standard
    library.

    Failure modes are surfaced as Blender ``{'ERROR'}`` reports and the
    operator returns ``{'CANCELLED'}`` so the menu can show an error
    indication and the browser is not launched (Requirement 11.5,
    11.6):

    * URL absent, non-string, or fails URL format validation:
      report ``"invalid documentation URL"``.
    * :func:`webbrowser.open` raises any exception or returns
      ``False``: report ``"could not open browser"``.

    The operator carries ``bl_options = {"INTERNAL"}`` because it is a
    dispatch target for the launcher menu and the preferences UI; it
    has no place in Blender's ``F3`` operator search.
    """

    bl_idname = "aitk.open_documentation"
    bl_label = "Open Documentation"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        """Return ``True`` once the SettingsStore has been bound.

        Between module import and the addon's top-level
        ``register()`` call to :func:`bind_settings`, the store
        reference is ``None`` and there is no documentation URL to
        consult, so the operator must not run.
        """
        return _settings is not None

    def execute(self, context):
        """Validate the URL and dispatch it to the user's web browser.

        Three gates, in order:

        1. The settings store must be bound. If a stray ``execute``
           somehow slips past :meth:`poll`, fail loud with an error
           report rather than crash.
        2. The URL must be a non-empty string and must pass
           :func:`is_valid_url`. Anything else triggers
           ``"invalid documentation URL"`` and a warning log naming
           the offending value (Requirement 11.5).
        3. :func:`webbrowser.open` must return truthy without raising.
           Both ``False`` and any exception map to
           ``"could not open browser"`` (Requirement 11.6). The
           exception case is logged via ``logger.exception`` so the
           original traceback is preserved for debugging.

        Returns ``{'FINISHED'}`` only when the browser was launched
        successfully; every error path returns ``{'CANCELLED'}``.
        """
        if _settings is None:
            self.report({"ERROR"}, "AI Toolkit is not yet ready")
            return {"CANCELLED"}

        url = _settings.get(DOCUMENTATION_URL)
        if not isinstance(url, str) or not is_valid_url(url):
            self.report({"ERROR"}, "invalid documentation URL")
            logger.warning(
                "AITK_OT_open_documentation: invalid URL %r", url
            )
            return {"CANCELLED"}

        try:
            opened = webbrowser.open(url)
        except Exception:
            logger.exception(
                "AITK_OT_open_documentation: webbrowser.open(%r) raised",
                url,
            )
            self.report({"ERROR"}, "could not open browser")
            return {"CANCELLED"}

        if not opened:
            logger.warning(
                "AITK_OT_open_documentation: webbrowser.open(%r) returned False",
                url,
            )
            self.report({"ERROR"}, "could not open browser")
            return {"CANCELLED"}

        return {"FINISHED"}


class AITK_OT_open_preferences(bpy.types.Operator):
    """Open the AI Toolkit Platform addon preferences window.

    Wired to the "Settings" entry of the Launcher_Menu (Requirements
    2.7). Blender's ``PREFERENCES_OT_addon_show`` operator is the
    standard entry point for "scroll the addon-preferences list to a
    specific addon and expand it" — it both opens the User Preferences
    window if it is not already open and switches to the Add-ons tab
    filtered on the supplied module name.

    Implementation strategy:

    1. Prefer ``bpy.ops.preferences.addon_show(module=ADDON_PACKAGE_NAME)``
       so the user lands directly on this addon's expanded row.
    2. Fall back to ``bpy.ops.screen.userpref_show()`` if Blender does
       not expose ``addon_show`` (older builds, headless contexts, or
       a future API rename). The user reaches the User Preferences
       window but is not auto-scrolled to this addon — still useful.
    3. If neither operator is available, report
       ``"could not open preferences"`` and return ``{'CANCELLED'}``.
       Any exception thrown by the dispatch is caught, logged with a
       full traceback, and surfaced as the same error report so a
       broken Blender API does not crash the menu.

    The operator carries ``bl_options = {"INTERNAL"}`` because it is a
    pure dispatch target for the launcher menu.
    """

    bl_idname = "aitk.open_preferences"
    bl_label = "Open Preferences"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        """Dispatch to ``bpy.ops.preferences.addon_show`` or its fallback.

        Uses :func:`getattr` rather than direct attribute access so a
        Blender build that has either operator missing surfaces as a
        clean fallback path instead of an :class:`AttributeError`. The
        whole body is wrapped in a try/except: ``bpy.ops`` operators
        can raise ``RuntimeError`` (no suitable context, the Blender
        window manager has not initialised, etc.) and a crash here
        would leave the launcher menu dangling.
        """
        try:
            ops_preferences = getattr(bpy.ops, "preferences", None)
            addon_show = (
                getattr(ops_preferences, "addon_show", None)
                if ops_preferences is not None
                else None
            )
            if callable(addon_show):
                addon_show(module=ADDON_PACKAGE_NAME)
                return {"FINISHED"}

            ops_screen = getattr(bpy.ops, "screen", None)
            userpref_show = (
                getattr(ops_screen, "userpref_show", None)
                if ops_screen is not None
                else None
            )
            if callable(userpref_show):
                userpref_show()
                return {"FINISHED"}

            self.report({"ERROR"}, "could not open preferences")
            logger.error(
                "AITK_OT_open_preferences: neither preferences.addon_show "
                "nor screen.userpref_show is available on bpy.ops"
            )
            return {"CANCELLED"}
        except Exception:
            logger.exception(
                "AITK_OT_open_preferences: failed to open preferences"
            )
            self.report({"ERROR"}, "could not open preferences")
            return {"CANCELLED"}


__all__ = [
    "AITK_OT_launcher_click_router",
    "AITK_OT_open_documentation",
    "AITK_OT_open_preferences",
    "ADDON_PACKAGE_NAME",
    "bind_settings",
]
