# ui/panels/fallback_sidebar.py
"""Fallback sidebar panel that surfaces the launcher menu when the overlay is off.

Per Requirement 1.11, when the user has disabled the floating launcher
overlay, an alternative entry point must remain available so the user
can still reach the addon's modules. This panel is rendered in the
"AI Toolkit" sidebar tab and is visible iff
``LAUNCHER_OVERLAY_ENABLED == False``.

Wiring
------
The addon entry-point (:func:`ai_toolkit.__init__.register`, task 24.1)
is responsible for registering :class:`AITK_PT_fallback_sidebar` with
Blender and for calling :func:`bind_settings` once the bpy-free
:class:`SettingsStore` has been instantiated. Until ``bind_settings``
runs, :meth:`AITK_PT_fallback_sidebar.poll` returns ``False`` so the
panel stays hidden during the brief window between class registration
and settings binding -- the floating launcher itself is not yet drawing
during that window either.

Validates: Requirement 1.11.
"""

from __future__ import annotations

import logging
from typing import Optional

import bpy

from ...services.settings.schema import LAUNCHER_OVERLAY_ENABLED
from ...services.settings.store import SettingsStore

logger = logging.getLogger("ai_toolkit")


# Module-level reference. Bound by the addon ``__init__.register()``
# (task 24.1) once the bpy-free ``SettingsStore`` has been built. Held
# at module scope so :meth:`AITK_PT_fallback_sidebar.poll` -- which
# Blender invokes as a classmethod with no addon-context handle -- can
# consult the same store the rest of the UI layer uses.
_settings: Optional[SettingsStore] = None


def bind_settings(settings: SettingsStore) -> None:
    """Bind the :class:`SettingsStore` the panel consults.

    Idempotent: re-binding replaces the previous reference so addon
    reloads (which re-instantiate ``SettingsStore``) leave the panel
    pointing at the live store. Calling this after ``unregister`` is
    a no-op until the next ``register`` calls it again with the new
    store.
    """
    global _settings
    _settings = settings


class AITK_PT_fallback_sidebar(bpy.types.Panel):
    """Sidebar panel shown when the floating launcher overlay is disabled.

    Hosts a single "Open AI Toolkit" button that invokes
    ``aitk.launcher_menu`` -- the same operator id the click router
    triggers when the floating launcher is clicked. This guarantees the
    user always has a path to the Launcher_Menu (Requirement 1.11),
    even when they have turned the floating overlay off in preferences.
    """

    bl_idname = "AITK_PT_fallback_sidebar"
    bl_label = "AI Toolkit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    @classmethod
    def poll(cls, context):
        """Visible iff the overlay is disabled (Req 1.11).

        Returns ``False`` when ``_settings`` has not yet been bound so
        the panel stays hidden during the transient window between
        registration of the class and the addon's :func:`bind_settings`
        call. The overlay is not yet drawing during that window either,
        so a brief total absence of any entry point is the expected
        intermediate state.

        Uses ``not bool(...)`` so a missing or weirdly-typed value in
        the backing store surfaces as a hidden panel rather than a
        stack trace propagated into Blender's UI loop.
        """
        if _settings is None:
            return False
        return not bool(_settings.get(LAUNCHER_OVERLAY_ENABLED))

    def draw(self, context):
        """Render the fallback entry point.

        Displays a short explanation, a single "Open AI Toolkit" button
        wired to ``aitk.launcher_menu``, and a hint pointing the user
        back to the preferences toggle they used to disable the
        overlay.
        """
        layout = self.layout
        layout.label(text="The launcher overlay is disabled.")
        layout.label(text="Open the AI Toolkit menu:")
        layout.operator(
            "aitk.launcher_menu",
            text="Open AI Toolkit",
            icon="MENU_PANEL",
        )
        layout.separator()
        layout.label(text="(Re-enable the overlay in Preferences)")


__all__ = [
    "AITK_PT_fallback_sidebar",
    "bind_settings",
]
