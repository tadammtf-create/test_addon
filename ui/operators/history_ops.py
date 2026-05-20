"""History panel operators: re-import and delete (task 21.2).

The History panel rendered in :mod:`ai_toolkit.ui.panels.history_panel`
draws two per-row controls: "Re-import" and "Delete". Both are wired to
operators defined here so the panel itself stays a thin layout shell
and every state mutation lives in a single, testable operator class.

* :class:`AITK_OT_history_reimport` -- looks up the entry by
  ``job_id``, asks the bpy-free
  :func:`~ai_toolkit.services.history.manager.reimport_entry` helper to
  plan a re-import, and on a planned plan dispatches each output path
  to :class:`~ai_toolkit.ui.asset_importer.AssetImporter` (Req 10.4).
  When the helper returns a
  :class:`~ai_toolkit.services.history.manager.ReimportError` the
  operator reports the generic
  ``"this entry cannot be re-imported"`` notification of Req 10.10 --
  the structured reason from the helper is logged at INFO level so a
  developer can still see *why* the re-import was refused.
* :class:`AITK_OT_history_delete` -- forwards the click to
  :meth:`HistoryManager.delete`. When :meth:`delete` returns ``False``
  (no such entry, e.g. the user clicked an already-deleted row whose
  panel hasn't redrawn yet) the operator surfaces the exact wording
  required by Req 10.6: ``"no history entry to delete"``.

Layer
-----
This module imports :mod:`bpy` and is part of the **UI Layer**. It
delegates every business decision to bpy-free service-layer helpers
(:func:`reimport_entry`, :class:`HistoryManager`) so the operators
themselves are little more than glue between Blender's operator
surface and the service layer's contract.

Module-level binding
--------------------
The operators read both their dependencies from module-level slots
populated by :func:`bind_dependencies`, which the addon's top-level
``__init__.register()`` (task 24.1) calls once after constructing the
:class:`HistoryManager` and the :class:`AssetImporter`. The same
convention is used by
:func:`ai_toolkit.ui.panels.history_panel.bind_history_manager` so the
panel and the operators always observe the same store. Looking up the
:class:`HistoryManager` once at register time -- rather than walking
``bpy.context.preferences.addons[...]`` on every click -- keeps the
operator implementation easy to unit-test without Blender running and
removes a per-event lookup hop.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import bpy

from ...services.history.manager import (
    HistoryManager,
    ReimportError,
    ReimportPlan,
    reimport_entry,
)
from ..asset_importer import SUPPORTED_3D, SUPPORTED_IMAGE, AssetImporter


# Single addon-wide logger; same handle the service layer uses.
logger = logging.getLogger("ai_toolkit")


# Module-level handles bound by addon ``__init__.register()`` (task
# 24.1). The history panel binds to the same :class:`HistoryManager`
# via :func:`ui.panels.history_panel.bind_history_manager`, so both the
# panel listing and the per-row operators always observe the same
# store. Both slots are ``None`` between module import and the bind
# call (and again briefly after a hot reload before re-bind), and the
# operators surface that state as a transient "not yet ready"
# notification rather than crashing.
_history_manager: Optional[HistoryManager] = None
_asset_importer: Optional[AssetImporter] = None


def bind_dependencies(history: HistoryManager, importer: AssetImporter) -> None:
    """Bind the :class:`HistoryManager` and :class:`AssetImporter` the operators consult.

    Called by the addon's top-level ``__init__.register()`` (task 24.1)
    once both dependencies have been constructed and before any history
    panel is registered. Idempotent: a subsequent call replaces the
    references, which is the desired behaviour when Blender reloads
    the addon and the previous instances are discarded.

    Both arguments are accepted as the live, shared instances; the
    operators never construct their own :class:`HistoryManager` so
    every history mutation goes through the single store the rest of
    the addon already mirrors on disk.
    """
    global _history_manager, _asset_importer
    _history_manager = history
    _asset_importer = importer


def _ext_of(file_path: str) -> str:
    """Return the lowercase file extension (including the leading dot).

    Mirrors the helper in :mod:`ai_toolkit.ui.asset_importer` so the
    history operator's "is this a 3D model or an image?" decision uses
    the same case-insensitive logic as the importer itself. Pulled out
    here as a tiny private helper rather than imported from the asset
    importer module so the operator stays single-purpose.
    """
    return os.path.splitext(file_path)[1].lower()


class AITK_OT_history_reimport(bpy.types.Operator):
    """Re-import every output file referenced by a history entry (Req 10.4, 10.10).

    Reads ``self.job_id`` (set by the panel button), looks the entry up
    in the bound :class:`HistoryManager`, and asks the pure-logic
    :func:`reimport_entry` helper to plan the re-import. The helper
    enforces every Req 10.4 precondition (status is ``"succeeded"``,
    every output file exists on disk, and the importer accepts each
    extension) so this operator only has to interpret the result:

    * :class:`ReimportPlan` -- iterate ``plan.file_paths`` and
      dispatch each to :meth:`AssetImporter.import_model` or
      :meth:`AssetImporter.import_image` based on the file extension.
      Per-file failures are caught and the operator reports
      ``"Re-imported {success_count} of {total} files"`` so partial
      success is visible to the user.
    * :class:`ReimportError` -- emit the Req 10.10 generic
      ``"this entry cannot be re-imported"`` notification. The
      structured reason and (when applicable) the ``missing_paths``
      tuple are logged at INFO level so a developer can still see
      *why* without surfacing implementation detail to the user.

    ``bl_options`` carries ``INTERNAL`` so the operator does not appear
    in Blender's ``F3`` operator search or the user's spacebar menu --
    it is wiring driven by the History panel's per-row buttons, not a
    user-facing command. ``REGISTER`` is included so Blender records
    the operator in its undo stack the same way it does for other
    panel-driven operators in the addon.
    """

    bl_idname = "aitk.history_reimport"
    bl_label = "Re-import"
    bl_options = {"INTERNAL", "REGISTER"}

    job_id: bpy.props.StringProperty(
        name="Job ID",
        description="Identifier of the history entry to re-import",
        default="",
    )

    def execute(self, context):
        """Resolve the entry, plan the re-import, and dispatch each file.

        Five steps, in order:

        1. Bail with ``"AI Toolkit history is not yet ready"`` if
           either dependency hasn't been bound yet -- Blender can
           invoke an operator while the addon is mid-register or
           mid-reload, and crashing on a missing dependency would
           leave the panel in an unrecoverable state.
        2. Look the entry up by ``job_id`` in the bound
           :class:`HistoryManager`. A miss means the panel was looking
           at a stale list (e.g. the user clicked a row that another
           tab already deleted); surface
           ``"no history entry to re-import"`` so the user knows to
           refresh.
        3. Hand the entry to :func:`reimport_entry` together with the
           live :class:`AssetImporter`. The helper applies every Req
           10.4 precondition and returns either a plan or a structured
           error.
        4. On :class:`ReimportError`, emit the Req 10.10 generic
           notification and log the structured reason at INFO so it
           survives in the addon log.
        5. On :class:`ReimportPlan`, iterate ``plan.file_paths`` and
           call the matching :class:`AssetImporter` method based on
           file extension. Each call is wrapped in a try/except so a
           single failing file does not abort the rest of the plan;
           the operator reports the success count over the total at
           the end so partial success is visible.
        """
        if _history_manager is None or _asset_importer is None:
            self.report({"ERROR"}, "AI Toolkit history is not yet ready")
            return {"CANCELLED"}

        # Linear scan over list_entries(): the History panel never
        # holds more than MAX_ENTRIES (500) records, so the cost is
        # trivial and avoids a separate by-id index that would have to
        # be kept in sync with the in-memory list.
        entry = None
        for candidate in _history_manager.list_entries():
            if candidate.job_id == self.job_id:
                entry = candidate
                break
        if entry is None:
            self.report({"ERROR"}, "no history entry to re-import")
            return {"CANCELLED"}

        result = reimport_entry(entry, _asset_importer)

        if isinstance(result, ReimportError):
            # Req 10.10: surface a single generic notification. The
            # structured reason (and any missing/rejected paths) stays
            # in the log so a developer can diagnose without exposing
            # implementation detail to the user.
            self.report({"WARNING"}, "this entry cannot be re-imported")
            logger.info(
                "Re-import refused for %s: %s%s",
                self.job_id,
                result.reason,
                f" missing={list(result.missing_paths)}"
                if result.missing_paths
                else "",
            )
            return {"CANCELLED"}

        # ``isinstance(result, ReimportPlan)`` at this point.
        success_count = self._dispatch_plan(result)
        total = len(result.file_paths)
        self.report(
            {"INFO"},
            f"Re-imported {success_count} of {total} files",
        )
        return {"FINISHED"}

    def _dispatch_plan(self, plan: ReimportPlan) -> int:
        """Dispatch every file in ``plan`` to :class:`AssetImporter`.

        Returns the number of files imported successfully. Per-file
        failures are logged and counted as a miss so partial success
        is still surfaced to the user via the operator's INFO report
        rather than being silently lost or aborting the rest of the
        plan.

        The 3D / image split uses the same SUPPORTED_3D /
        SUPPORTED_IMAGE sets that :class:`AssetImporter` itself
        consults, so a file the importer accepts here is guaranteed
        to be routed to the matching
        :meth:`~AssetImporter.import_model` or
        :meth:`~AssetImporter.import_image` method. A path with an
        unrecognised extension is not silently skipped -- it is
        counted as a failure so the success/total ratio reported to
        the user accurately reflects what happened.
        """
        importer = _asset_importer
        if importer is None:
            # Defensive: ``execute`` already gated on this, but a
            # caller invoking ``_dispatch_plan`` directly should still
            # see a clean zero rather than an :class:`AttributeError`.
            return 0

        success_count = 0
        for path in plan.file_paths:
            ext = _ext_of(path)
            try:
                if ext in SUPPORTED_3D:
                    importer.import_model(path)
                elif ext in SUPPORTED_IMAGE:
                    importer.import_image(path)
                else:
                    logger.warning(
                        "Re-import: skipping %s (unsupported extension %r)",
                        path,
                        ext,
                    )
                    continue
            except Exception:
                # ``AssetImporter`` already logs and notifies on
                # failure; the operator only needs to keep going so
                # the rest of the plan still runs and the success
                # count is honest.
                logger.exception("Re-import: failed to import %s", path)
                continue
            success_count += 1
        return success_count


class AITK_OT_history_delete(bpy.types.Operator):
    """Delete a history entry (Req 10.5, 10.6).

    Reads ``self.job_id`` (set by the panel button) and forwards it to
    :meth:`HistoryManager.delete`. The store implements Req 10.5
    ("retain the underlying output files on disk") natively -- delete
    only touches the JSON index. When :meth:`delete` returns ``False``
    the operator reports the exact Req 10.6 wording:
    ``"no history entry to delete"``.

    On a successful delete the operator walks every ``VIEW_3D`` area
    in the current screen and calls :meth:`Area.tag_redraw` so the
    History panel reflects the change immediately, without waiting for
    Blender's next routine redraw.

    ``bl_options`` carries ``INTERNAL`` so the operator does not show
    up in operator search; ``REGISTER`` enrols it in the undo stack
    the same way the rest of the addon's panel-driven operators are
    enrolled.
    """

    bl_idname = "aitk.history_delete"
    bl_label = "Delete"
    bl_options = {"INTERNAL", "REGISTER"}

    job_id: bpy.props.StringProperty(
        name="Job ID",
        description="Identifier of the history entry to delete",
        default="",
    )

    def execute(self, context):
        """Forward the click to :meth:`HistoryManager.delete` and refresh the panel.

        Three branches:

        * Dependency not yet bound (addon mid-register or mid-reload):
          report ``"AI Toolkit history is not yet ready"`` and bail.
        * :meth:`delete` returns ``False`` (Req 10.6: no such entry):
          report the exact required wording
          ``"no history entry to delete"`` as a ``WARNING`` so the user
          notices but the operator is still treated as
          ``{"CANCELLED"}`` for undo-stack purposes.
        * :meth:`delete` returns ``True``: tag every ``VIEW_3D`` area
          in the current screen for redraw so the History panel
          (which lives inside an N-panel sidebar in a 3D Viewport)
          reflects the delete immediately, then report a success
          ``INFO`` notification.
        """
        if _history_manager is None:
            self.report({"ERROR"}, "AI Toolkit history is not yet ready")
            return {"CANCELLED"}

        try:
            removed = _history_manager.delete(self.job_id)
        except Exception:
            logger.exception(
                "History delete: HistoryManager.delete(%r) raised",
                self.job_id,
            )
            self.report({"ERROR"}, "Failed to delete history entry")
            return {"CANCELLED"}

        if not removed:
            # Req 10.6 verbatim wording.
            self.report({"WARNING"}, "no history entry to delete")
            return {"CANCELLED"}

        self._tag_view3d_redraw(context)
        self.report({"INFO"}, "Deleted history entry")
        return {"FINISHED"}

    @staticmethod
    def _tag_view3d_redraw(context) -> None:
        """Force every ``VIEW_3D`` area in the current screen to redraw.

        The History panel lives inside the AI Toolkit N-panel category
        of the 3D Viewport sidebar, so tagging ``VIEW_3D`` areas is
        sufficient to refresh it. Wrapped in defensive ``getattr`` /
        try-except so a missing ``context.screen`` (which can happen
        during very early register paths or in headless tests) never
        breaks the delete flow -- the data is already gone from the
        store, and the next routine redraw will pick it up regardless.
        """
        try:
            screen = getattr(context, "screen", None)
            if screen is None:
                return
            for area in getattr(screen, "areas", ()) or ():
                if getattr(area, "type", None) == "VIEW_3D":
                    tag_redraw = getattr(area, "tag_redraw", None)
                    if callable(tag_redraw):
                        tag_redraw()
        except Exception:
            logger.exception("History delete: tag_redraw failed")


__all__ = [
    "AITK_OT_history_reimport",
    "AITK_OT_history_delete",
    "bind_dependencies",
]
