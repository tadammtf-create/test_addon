"""Text-to-3D PropertyGroup, panel, and "Show panel" operator.

This module is part of the **UI Layer**: it imports :mod:`bpy` at the
top level and is responsible for the Blender-facing surface of the
Text-to-3D Generation_Module (Requirements 4.1, 4.3, 4.6). Three
classes live here:

* :class:`AITK_PG_text_to_3d` — the per-scene
  :class:`bpy.types.PropertyGroup` that holds the prompt text, the
  current job status, the latest failure reason, and a reference to
  the active :class:`~ai_toolkit.services.jobs.handle.JobHandle` (kept
  as a job id string because :class:`bpy.types.PropertyGroup` cannot
  store arbitrary Python objects). The class is **defined** here but
  the ``Scene.ai_toolkit_text_to_3d`` pointer-property registration
  that attaches it to every scene is performed by the addon's
  top-level ``__init__.register()`` (task 24.1) so the property
  persists with the .blend file.

* :class:`AITK_OT_show_text_to_3d_panel` — the operator the launcher
  menu's :data:`~ai_toolkit.ui.launcher_menu.MODULE_REGISTRY` invokes
  when the user picks "Text-to-3D" (``bl_idname`` is
  ``aitk.show_text_to_3d_panel``, matching the registry entry). The
  panel below is a normal sidebar panel and is therefore always
  available under the "AI Toolkit" tab; this operator's job is
  best-effort: tag the active 3D Viewport for a redraw and report
  guidance to the user, since reliably switching the active sidebar
  category programmatically across Blender versions is fragile.

* :class:`AITK_PT_text_to_3d_panel` — the
  :class:`bpy.types.Panel` rendered in the 3D Viewport's "AI Toolkit"
  N-panel tab. The panel reads from
  ``context.scene.ai_toolkit_text_to_3d`` and renders four UI rows:
  prompt input, Generate button, status / error label, and Cancel
  button. The cancel-enabled rule is exactly Requirement 4.6: enabled
  iff status is ``queued`` or ``running``.

The submit operator (``aitk.submit_text_to_3d``) and the cancel
operator (``aitk.cancel_text_to_3d``) referenced by the panel's
``draw()`` are implemented in task 16.2 (``ui/operators/job_ops.py``).
Calling ``layout.operator(<unregistered_id>)`` mid-development renders
a disabled button with a warning icon, which is acceptable while the
two tasks land independently.

Validates: Requirements 4.1, 4.3, 4.6.

.. note::
    This module deliberately omits ``from __future__ import annotations``.
    PEP 563 string-stringifies all class-level annotations, which would
    leave :class:`AITK_PG_text_to_3d`'s
    ``prompt: bpy.props.StringProperty(...)`` declarations as plain
    strings in ``__annotations__`` instead of executing the
    :func:`bpy.props.StringProperty` calls at class-definition time.
    Blender's :class:`bpy.types.PropertyGroup` registration walks
    ``__annotations__`` expecting the real property descriptors, so
    enabling PEP 563 here would silently break the addon's per-scene
    state. The legacy ``properties.py`` follows the same convention.
"""

import logging

import bpy


# Single addon-wide logger; same handle the service layer uses.
logger = logging.getLogger("ai_toolkit")


# ---------------------------------------------------------------------------
# Status label set.
# ---------------------------------------------------------------------------
#
# The five active-job labels from Requirement 4.3 plus the addon-local
# ``idle`` sentinel that means "no job running for this module". The
# panel uses this set to decide when to enable the Cancel button
# (Requirement 4.6 / Property 10): cancel is enabled iff status is in
# the active subset ``{queued, running}``.
_ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})


# ---------------------------------------------------------------------------
# PropertyGroup.
# ---------------------------------------------------------------------------


class AITK_PG_text_to_3d(bpy.types.PropertyGroup):
    """Per-scene state for the Text-to-3D Generation_Module.

    Attached to :class:`bpy.types.Scene` as ``ai_toolkit_text_to_3d``
    by the addon's top-level ``__init__.register()`` (task 24.1) via
    ``Scene.ai_toolkit_text_to_3d = bpy.props.PointerProperty(...)``.
    Storing the state on :class:`Scene` means it survives a .blend
    save/load cycle, which is the natural fit for the prompt the user
    is composing and the most recent job's status.

    Field rationale:

    * ``prompt`` — bounded at 1000 characters by ``maxlen`` to satisfy
      Requirement 4.1's "1 to 1000 characters inclusive" upper bound at
      input time. Lower-bound and whitespace-only validation is done
      by the submit operator in task 16.2.
    * ``status`` — exactly one of ``idle``, ``queued``, ``running``,
      ``succeeded``, ``failed``, or ``cancelled``. ``idle`` is the
      addon-local default for "no job running"; the other five match
      Requirement 4.3 verbatim.
    * ``failure_reason`` — populated when ``status`` is ``failed`` so
      the panel can surface the cause (Requirement 4.10). Empty
      string when there is nothing to report.
    * ``current_job_id`` — the job_id of the
      :class:`~ai_toolkit.services.jobs.handle.JobHandle` returned by
      the submit operator. We store the id (a string) rather than the
      handle object itself because
      :class:`bpy.types.PropertyGroup` only accepts Blender-native
      property types; the submit operator (task 16.2) keeps the live
      handle in a module-level dict keyed by job id and resolves it
      from this field as needed.
    """

    prompt: bpy.props.StringProperty(
        name="Prompt",
        default="",
        maxlen=1000,
        description="Text description of the 3D model to generate",
    )
    status: bpy.props.StringProperty(
        name="Status",
        default="idle",
        description=(
            "Current job status: idle, queued, running, succeeded, "
            "failed, or cancelled"
        ),
    )
    failure_reason: bpy.props.StringProperty(
        name="Failure",
        default="",
        description="Reason a failed job reported, if any",
    )
    current_job_id: bpy.props.StringProperty(
        name="Job",
        default="",
        description="Job id of the currently tracked Text-to-3D job",
    )


# ---------------------------------------------------------------------------
# "Show panel" operator (target of the launcher menu's MODULE_REGISTRY).
# ---------------------------------------------------------------------------


class AITK_OT_show_text_to_3d_panel(bpy.types.Operator):
    """Surface the Text-to-3D panel for the user.

    Invoked by the
    :data:`~ai_toolkit.ui.launcher_menu.MODULE_REGISTRY` entry
    ``("Text-to-3D", "aitk.show_text_to_3d_panel")`` when the user
    picks Text-to-3D in the launcher menu (Requirement 2.3). The
    matching panel :class:`AITK_PT_text_to_3d_panel` is always
    registered under the "AI Toolkit" sidebar tab, so this operator's
    real job is a best-effort nudge: tag the active 3D Viewport for a
    redraw and report guidance to the user. Reliably *switching* the
    sidebar's active category programmatically requires touching
    ``context.space_data.show_region_ui`` and a workspace-tab
    activation that is fragile across Blender versions; that
    cross-version switching is intentionally out of scope for this
    task and can be layered in later without changing the operator's
    public surface.

    The ``bl_options = {"INTERNAL"}`` flag keeps this operator out of
    Blender's ``F3`` operator search and the spacebar menu — it is
    wiring driven by the launcher menu, not a user-facing command.
    """

    bl_idname = "aitk.show_text_to_3d_panel"
    bl_label = "Show Text-to-3D"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        """Tag every 3D Viewport for redraw and report guidance.

        Wrapped in a top-level try/except: a failure inside Blender's
        ``area.regions`` or ``tag_redraw`` paths must not crash the
        launcher menu's dispatch. On unexpected failure we log with a
        stack trace, surface an ``{'ERROR'}`` report so the menu can
        render its inline error banner (Requirement 2.10), and
        return ``{'CANCELLED'}``.
        """
        try:
            screen = getattr(context, "screen", None)
            areas = getattr(screen, "areas", ()) if screen is not None else ()
            for area in areas:
                if getattr(area, "type", None) != "VIEW_3D":
                    continue
                # The N-panel sidebar lives in the ``UI`` region. We
                # cannot reliably *activate* the AI Toolkit category
                # from here across Blender versions, so we only tag the
                # region (and the area) for redraw and rely on the user
                # having the sidebar open.
                for region in getattr(area, "regions", ()) or ():
                    if getattr(region, "type", None) == "UI":
                        region.tag_redraw()
                        break
                area.tag_redraw()
            self.report(
                {"INFO"},
                "Open the AI Toolkit sidebar to see Text-to-3D",
            )
            return {"FINISHED"}
        except Exception:
            logger.exception("Failed to show Text-to-3D panel")
            self.report({"ERROR"}, "Could not show Text-to-3D panel")
            return {"CANCELLED"}


# ---------------------------------------------------------------------------
# Sidebar Panel.
# ---------------------------------------------------------------------------


class AITK_PT_text_to_3d_panel(bpy.types.Panel):
    """Sidebar panel for the Text-to-3D Generation_Module.

    Lives in the 3D Viewport's "AI Toolkit" N-panel tab so the panel
    is reachable both from the launcher menu (via
    :class:`AITK_OT_show_text_to_3d_panel`) and from the fallback
    sidebar entry point Requirement 1.11 mandates.

    Layout (top to bottom):

    1. Prompt input (Requirement 4.1).
    2. ``Generate`` row that invokes the submit operator from task
       16.2. The row is enabled only when the module is not already
       running a job — i.e. status is in
       ``{idle, succeeded, failed, cancelled}`` — so the user cannot
       accidentally fire a second submission while the first is still
       active.
    3. Status label rendering the current ``status`` string verbatim
       (Requirement 4.3).
    4. Optional error row when ``failure_reason`` is non-empty
       (Requirement 4.10).
    5. ``Cancel`` row that invokes the cancel operator from task 16.2.
       The row is enabled iff status is ``queued`` or ``running``,
       which is exactly Requirement 4.6 / Property 10.

    The ``Generate`` and ``Cancel`` operators (``aitk.submit_text_to_3d``
    and ``aitk.cancel_text_to_3d``) are implemented in task 16.2.
    Until that task lands, ``layout.operator(<unregistered_id>)``
    renders a disabled button with a warning icon — acceptable
    mid-development and reverts automatically once the operators are
    registered.
    """

    bl_idname = "AITK_PT_text_to_3d_panel"
    bl_label = "Text-to-3D"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    def draw(self, context):
        """Render the panel.

        Defensive against a partially-registered addon: when the
        ``Scene.ai_toolkit_text_to_3d`` pointer-property has not been
        attached yet (for example, during a hot reload between
        ``bpy.utils.register_class(AITK_PG_text_to_3d)`` and the
        matching ``Scene.ai_toolkit_text_to_3d = ...`` assignment in
        the addon's top-level ``__init__.register()``), ``getattr``
        returns ``None`` and we render a single explanatory label
        instead of raising — which would otherwise spam Blender's
        console with a draw-time exception every redraw.
        """
        layout = self.layout
        scene = getattr(context, "scene", None)
        props = getattr(scene, "ai_toolkit_text_to_3d", None)
        if props is None:
            layout.label(
                text="Text-to-3D not initialised",
                icon="INFO",
            )
            return

        # 1) Prompt input. ``maxlen=1000`` on the StringProperty
        #    enforces the upper bound at input time (Requirement 4.1);
        #    lower-bound and whitespace-only validation runs at submit
        #    time in task 16.2.
        layout.prop(props, "prompt", text="Prompt")

        status = props.status

        # 2) Generate row. Disable while a job is already in flight so
        #    the user cannot submit a second job on top of the first.
        submit_row = layout.row()
        submit_row.enabled = status not in _ACTIVE_STATUSES
        submit_row.operator(
            "aitk.submit_text_to_3d",
            text="Generate",
            icon="PLAY",
        )

        # 3) Status label (Requirement 4.3).
        layout.label(text="Status: " + status, icon="INFO")

        # 4) Optional error row (Requirement 4.10).
        if props.failure_reason:
            layout.label(
                text="Error: " + props.failure_reason,
                icon="ERROR",
            )

        # 5) Cancel row — Requirement 4.6 / Property 10.
        cancel_row = layout.row()
        cancel_row.enabled = status in _ACTIVE_STATUSES
        cancel_row.operator(
            "aitk.cancel_text_to_3d",
            text="Cancel",
            icon="CANCEL",
        )


__all__ = [
    "AITK_PG_text_to_3d",
    "AITK_OT_show_text_to_3d_panel",
    "AITK_PT_text_to_3d_panel",
]
