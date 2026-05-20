"""History panel that lists recorded ``Generation_Job`` records and dispatches re-import / delete.

The History panel is the user-facing surface for the
:class:`~ai_toolkit.services.history.manager.HistoryManager` (task 6.2).
It lives in the **UI Layer** and therefore imports :mod:`bpy` at module
top level, but it never reaches into ``bpy`` for state -- the entries it
renders come from a bound HistoryManager instance, which is part of the
``bpy``-free service layer (Requirement 13.1).

Layer split / state binding
---------------------------

The panel reads from a module-level ``_history_manager`` reference that
is populated by the addon's top-level ``__init__.register()`` (task
24.1) via :func:`bind_history_manager`. Routing every draw call through
``bpy.context.preferences.addons[...]`` would add a per-frame lookup
hop and tie this file to a concrete preferences class, which would in
turn make the smoke test for this module require a full Blender
preferences shim. Binding once at register time keeps both costs at
zero.

The actual re-import and delete operators (``aitk.history_reimport``,
``aitk.history_delete``) live in ``ui/operators/history_ops.py`` (task
21.2). They consult the same module-level handle that the panel reads
here, so a single :func:`bind_history_manager` call wires both surfaces
to the same store.

Validates: Requirements 10.3, 10.4, 10.5, 10.6, 10.10.
"""

from __future__ import annotations

import logging
from typing import Optional

import bpy

from ...services.history.manager import HistoryManager


logger = logging.getLogger("ai_toolkit")


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
#
# Bound by addon ``__init__.register()`` (task 24.1). ``None`` between
# module import and the bind call (and again after a hot reload before
# re-bind). Both the panel ``draw()`` and the history operators in
# ``ui/operators/history_ops.py`` (task 21.2) treat ``None`` as
# "history is not yet available" so a stray invocation cannot crash.
_history_manager: Optional[HistoryManager] = None


def bind_history_manager(history: Optional[HistoryManager]) -> None:
    """Bind the :class:`HistoryManager` the panel reads. Idempotent.

    Called by the addon's top-level ``__init__.register()`` (task 24.1)
    once the manager has been constructed against
    ``bpy.utils.user_resource('CONFIG') / 'ai_toolkit/history.json'``.
    Idempotent: a subsequent call replaces the reference, which is the
    desired behaviour when Blender reloads the addon and the previous
    manager instance is discarded.

    Passing ``None`` explicitly unbinds the manager, which is what the
    addon ``unregister()`` and the smoke test do to put the panel back
    into the "not yet available" state.
    """
    global _history_manager
    _history_manager = history


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class AITK_OT_show_history_panel(bpy.types.Operator):
    """Best-effort "Show History" entry point.

    The History panel is a regular ``bpy.types.Panel`` rendered in the
    ``VIEW_3D`` ``UI`` region under the ``AI Toolkit`` tab. Blender does
    not provide a programmatic way to scroll a specific panel into
    view, so this operator's role is to surface a notification telling
    the user where to find the panel and to tag the active 3D Viewport
    for a redraw so any panel collapse/expand state is refreshed.

    Carries ``bl_options = {"INTERNAL"}`` so the operator does not
    appear in Blender's ``F3`` operator search or the spacebar menu --
    it is wiring invoked from menus and the Launcher, not a
    user-facing command.
    """

    bl_idname = "aitk.show_history_panel"
    bl_label = "Show History"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        """Tag the active 3D Viewport for redraw and surface a notification."""
        try:
            screen = getattr(context, "screen", None)
            if screen is not None:
                for area in getattr(screen, "areas", []) or []:
                    if getattr(area, "type", None) == "VIEW_3D":
                        try:
                            area.tag_redraw()
                        except Exception:
                            # ``tag_redraw`` is best-effort; some test
                            # contexts pass a stub area without it.
                            logger.debug(
                                "Show History: tag_redraw failed",
                                exc_info=True,
                            )
                        break
            self.report({"INFO"}, "Open the AI Toolkit sidebar to see History")
            return {"FINISHED"}
        except Exception:
            logger.exception("Failed to show History panel")
            self.report({"ERROR"}, "Could not show History panel")
            return {"CANCELLED"}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


class AITK_PT_history_panel(bpy.types.Panel):
    """Lists recorded :class:`HistoryEntry` records with per-entry controls.

    Reads from the bound :class:`HistoryManager` via
    :func:`HistoryManager.list_entries`, which already returns entries
    sorted by ``created_at_iso8601`` descending (Requirement 10.3 -- see
    ``services/history/manager.py``). The panel does not re-sort.

    Each entry renders:

    * A header row with the task identifier and provider id.
    * Status, creation timestamp, and a truncated prompt preview (when
      present).
    * A summary count of output files (when present).
    * A Re-import / Delete control row.

    The Re-import and Delete controls are always *rendered* so the
    user has consistent feedback across entry states; the operators
    themselves
    (:class:`~ai_toolkit.ui.operators.history_ops.AITK_OT_history_reimport`
    and
    :class:`~ai_toolkit.ui.operators.history_ops.AITK_OT_history_delete`,
    task 21.2) handle the precondition gating defined in Requirements
    10.4, 10.6, and 10.10 inside their ``execute`` methods, surfacing a
    notification to the user when the entry is not re-importable or
    has already been deleted.
    """

    bl_idname = "AITK_PT_history_panel"
    bl_label = "History"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    def draw(self, context):
        """Render the panel.

        Three early-return branches keep the addon stable:

        * No bound manager (initialisation race or post-unregister)
          renders an "available shortly" hint.
        * A raised exception from ``list_entries()`` is logged once at
          ``error`` level and surfaces a "could not load history"
          label, so a corrupt history file (already logged by
          :class:`HistoryManager`) does not crash the addon.
        * An empty entry list renders a friendly "no history yet"
          hint instead of an empty box.
        """
        layout = self.layout

        if _history_manager is None:
            layout.label(text="History not yet available", icon="INFO")
            return

        try:
            entries = _history_manager.list_entries()
        except Exception:
            logger.exception("Failed to list history entries")
            layout.label(text="Could not load history", icon="ERROR")
            return

        if not entries:
            layout.label(text="No generation history yet", icon="INFO")
            return

        layout.label(text=f"{len(entries)} entries:")

        for entry in entries:
            box = layout.box()

            header = box.row()
            header.label(
                text=f"{entry.task} | {entry.provider_id}",
                icon="OUTLINER_DATA_VOLUME",
            )

            box.label(text=f"Status: {entry.status}")
            box.label(text=f"Created: {entry.created_at_iso8601}")
            if entry.prompt:
                # 60 chars is enough to glance the intent without
                # overflowing the sidebar's typical 320 px width.
                box.label(text=f"Prompt: {entry.prompt[:60]}")
            if entry.output_file_paths:
                box.label(
                    text=f"Outputs: {len(entry.output_file_paths)} file(s)"
                )

            controls = box.row(align=True)

            # ``layout.operator()`` returns the operator instance; we
            # set ``job_id`` on it so the receiving operator (task
            # 21.2, ``StringProperty(name='job_id')``) knows which
            # entry to act on. This is the standard Blender pattern
            # for parameterising a row-rendered operator.
            reimport_op = controls.operator(
                "aitk.history_reimport",
                text="Re-import",
                icon="IMPORT",
            )
            reimport_op.job_id = entry.job_id

            delete_op = controls.operator(
                "aitk.history_delete",
                text="Delete",
                icon="TRASH",
            )
            delete_op.job_id = entry.job_id


__all__ = [
    "AITK_PT_history_panel",
    "AITK_OT_show_history_panel",
    "bind_history_manager",
]
