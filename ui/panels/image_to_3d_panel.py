"""Image-to-3D PropertyGroup, panel, and "Show panel" operator.

This module is part of the **UI Layer**: it imports :mod:`bpy` at the
top level and is responsible for the Blender-facing surface of the
Image-to-3D Generation_Module (Requirements 5.1, 5.8, 5.9). Three
classes live here:

* :class:`AITK_PG_image_to_3d` -- the per-scene
  :class:`bpy.types.PropertyGroup` that holds the source image path,
  the current job status, the latest failure reason, and a reference
  to the active :class:`~ai_toolkit.services.jobs.handle.JobHandle`
  (kept as a job id string because :class:`bpy.types.PropertyGroup`
  cannot store arbitrary Python objects). The class is **defined**
  here but the ``Scene.ai_toolkit_image_to_3d`` pointer-property
  registration that attaches it to every scene is performed by the
  addon's top-level ``__init__.register()`` (task 24.1) so the
  property persists with the .blend file.

* :class:`AITK_OT_show_image_to_3d_panel` -- the operator the
  Launcher_Menu invokes when the user picks "Image-to-3D"
  (``bl_idname`` is ``aitk.show_image_to_3d_panel``, matching the
  registry entry in :data:`~ai_toolkit.ui.launcher_menu.MODULE_REGISTRY`).
  Its job is best-effort: tag the active 3D Viewport for redraw and
  report a hint, since reliably switching the active sidebar
  category programmatically is fragile across Blender versions.

* :class:`AITK_PT_image_to_3d_panel` -- the
  :class:`bpy.types.Panel` rendered in the 3D Viewport's "AI Toolkit"
  N-panel tab. The panel reads from
  ``context.scene.ai_toolkit_image_to_3d`` and renders the file
  picker, a thumbnail-preview row, the Generate button, the status
  / error label, and the Cancel button.

The image-extension filter, the 20 MB size check, and the decodability
check (Requirements 5.1, 5.3, 5.4, 5.11) are all enforced by the
submit operator in task 17.2 -- :class:`bpy.props.StringProperty` does
not accept a filter list directly. The panel's :meth:`draw` surfaces a
"Supported: .png, .jpg, .jpeg, .webp" hint label so the user knows the
allowed extensions before opening the file browser.

Status display refresh (Requirement 5.8: "within 2 seconds of each
provider status change") falls out of the platform's existing wiring:
:class:`~ai_toolkit.ui.job_dispatcher.AITK_OT_job_dispatcher` ticks
every 100 ms, the executor's status callback writes ``props.status`` on
the main thread, and the submit operator (task 17.2) calls
:meth:`Area.tag_redraw` so this panel re-runs :meth:`draw` and renders
the new label well inside the 2-second budget.

The submit operator (``aitk.submit_image_to_3d``) and the cancel
operator (``aitk.cancel_image_to_3d``) referenced by the panel's
``draw()`` are implemented in task 17.2 (``ui/operators/job_ops.py``).
Calling ``layout.operator(<unregistered_id>)`` mid-development renders
a disabled button with a warning icon, which is acceptable while the
two tasks land independently.

Validates: Requirements 5.1, 5.8, 5.9.

.. note::
    This module deliberately omits ``from __future__ import annotations``.
    PEP 563 string-stringifies all class-level annotations, which would
    leave :class:`AITK_PG_image_to_3d`'s
    ``image_path: bpy.props.StringProperty(...)`` declarations as plain
    strings in ``__annotations__`` instead of executing the
    :func:`bpy.props.StringProperty` calls at class-definition time.
    Blender's :class:`bpy.types.PropertyGroup` registration walks
    ``__annotations__`` expecting the real property descriptors, so
    enabling PEP 563 here would silently break the addon's per-scene
    state. The companion ``text_to_3d_panel.py`` from task 16.1
    follows the same convention.
"""

import logging

import bpy

from ...services.validation import fit_thumbnail


# Single addon-wide logger; same handle the service layer uses.
logger = logging.getLogger("ai_toolkit")


# ---------------------------------------------------------------------------
# Status label set.
# ---------------------------------------------------------------------------
#
# The five active-job labels from Requirement 5.8 plus the addon-local
# ``idle`` sentinel that means "no job running for this module". The
# panel uses this set to decide when to enable the Cancel button:
# cancel is enabled iff status is in the active subset
# ``{queued, running}`` (mirrors Requirement 4.6 / Property 10).
_ACTIVE_STATUSES = frozenset({"queued", "running"})

# Maximum thumbnail edge in pixels, matching :func:`fit_thumbnail`'s
# 256 px cap (Requirement 5.9). Declared here as a constant so the
# panel can include it in the user-visible "Preview: WxH" hint.
_THUMBNAIL_MAX = 256


# ---------------------------------------------------------------------------
# PropertyGroup.
# ---------------------------------------------------------------------------


class AITK_PG_image_to_3d(bpy.types.PropertyGroup):
    """Per-scene state for the Image-to-3D Generation_Module.

    Attached to :class:`bpy.types.Scene` as ``ai_toolkit_image_to_3d``
    by the addon's top-level ``__init__.register()`` (task 24.1) via
    ``Scene.ai_toolkit_image_to_3d = bpy.props.PointerProperty(...)``.
    Storing the state on :class:`Scene` means it survives a .blend
    save/load cycle, which is the natural fit for the source image
    the user has selected and the most recent job's status.

    Field rationale:

    * ``image_path`` -- ``subtype="FILE_PATH"`` makes Blender render a
      file-browser button in the panel. The ``.png/.jpg/.jpeg/.webp``
      extension filter mandated by Requirement 5.1 is enforced by the
      submit operator in task 17.2 because
      :class:`bpy.props.StringProperty` does not accept a per-property
      extension whitelist. The panel surfaces a "Supported: ..." hint
      label adjacent to the picker.
    * ``status`` -- exactly one of ``idle``, ``queued``, ``running``,
      ``succeeded``, ``failed``, or ``cancelled``. ``idle`` is the
      addon-local default for "no job running"; the other five match
      Requirement 5.8 verbatim.
    * ``failure_reason`` -- populated when ``status`` is ``failed`` so
      the panel can surface the cause (Requirement 5.10). Empty
      string when there is nothing to report.
    * ``current_job_id`` -- the job_id of the
      :class:`~ai_toolkit.services.jobs.handle.JobHandle` returned by
      the submit operator. We store the id (a string) rather than the
      handle object itself because
      :class:`bpy.types.PropertyGroup` only accepts Blender-native
      property types; the submit operator (task 17.2) keeps the live
      handle in a module-level dict keyed by job id and resolves it
      from this field as needed.
    """

    image_path: bpy.props.StringProperty(
        name="Image",
        default="",
        subtype="FILE_PATH",
        description=(
            "Path to the source image (.png/.jpg/.jpeg/.webp, "
            "<= 20 MB)"
        ),
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
        description="Job id of the currently tracked Image-to-3D job",
    )


# ---------------------------------------------------------------------------
# "Show panel" operator (target of the launcher menu's MODULE_REGISTRY).
# ---------------------------------------------------------------------------


class AITK_OT_show_image_to_3d_panel(bpy.types.Operator):
    """Surface the Image-to-3D panel for the user.

    Invoked by the
    :data:`~ai_toolkit.ui.launcher_menu.MODULE_REGISTRY` entry
    ``("Image-to-3D", "aitk.show_image_to_3d_panel")`` when the user
    picks Image-to-3D in the launcher menu (Requirement 2.3). The
    matching panel :class:`AITK_PT_image_to_3d_panel` is always
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
    Blender's ``F3`` operator search and the spacebar menu -- it is
    wiring driven by the launcher menu, not a user-facing command.
    """

    bl_idname = "aitk.show_image_to_3d_panel"
    bl_label = "Show Image-to-3D"
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
                "Open the AI Toolkit sidebar to see Image-to-3D",
            )
            return {"FINISHED"}
        except Exception:
            logger.exception("Failed to show Image-to-3D panel")
            self.report({"ERROR"}, "Could not show Image-to-3D panel")
            return {"CANCELLED"}


# ---------------------------------------------------------------------------
# Sidebar Panel.
# ---------------------------------------------------------------------------


class AITK_PT_image_to_3d_panel(bpy.types.Panel):
    """Sidebar panel for the Image-to-3D Generation_Module.

    Lives in the 3D Viewport's "AI Toolkit" N-panel tab so the panel
    is reachable both from the launcher menu (via
    :class:`AITK_OT_show_image_to_3d_panel`) and from the fallback
    sidebar entry point Requirement 1.11 mandates.

    Layout (top to bottom):

    1. Image-path file picker (Requirement 5.1). Blender renders a
       file-browser button because the underlying StringProperty has
       ``subtype='FILE_PATH'``.
    2. "Supported: .png, .jpg, .jpeg, .webp" hint label -- the
       extension filter is enforced by the submit operator (task
       17.2) since StringProperty doesn't accept an extension list.
    3. Best-effort thumbnail-preview row (Requirement 5.9). The row
       calls :meth:`_compute_thumbnail_size` which uses
       :func:`~ai_toolkit.services.validation.fit_thumbnail` to
       produce dimensions <= 256x256 px while preserving aspect
       ratio. The full GPU-backed preview rendering can be improved
       in a follow-up; the contract this task locks in is that
       :func:`fit_thumbnail` is always exercised when an image path
       is set, so the displayed dimensions never exceed 256 px on
       either edge.
    4. ``Generate`` row that invokes the submit operator from task
       17.2. The row is enabled only when the module is not already
       running a job -- i.e. status is in
       ``{idle, succeeded, failed, cancelled}`` -- so the user cannot
       accidentally fire a second submission while the first is still
       active.
    5. Status label rendering the current ``status`` string verbatim
       (Requirement 5.8). The 2-second refresh budget falls out of
       the platform's existing wiring: the
       :class:`~ai_toolkit.ui.job_dispatcher.AITK_OT_job_dispatcher`
       ticks every 100 ms and re-tags the active area for redraw.
    6. Optional error row when ``failure_reason`` is non-empty
       (Requirement 5.10).
    7. ``Cancel`` row that invokes the cancel operator from task 17.2.
       The row is enabled iff status is ``queued`` or ``running``,
       which is exactly the cancel-enabled rule established for the
       Text-to-3D module (Requirement 4.6 / Property 10) and applied
       here for visual consistency across the five Generation_Modules.

    The ``Generate`` and ``Cancel`` operators
    (``aitk.submit_image_to_3d`` and ``aitk.cancel_image_to_3d``) are
    implemented in task 17.2. Until that task lands,
    ``layout.operator(<unregistered_id>)`` renders a disabled button
    with a warning icon -- acceptable mid-development and reverts
    automatically once the operators are registered.
    """

    bl_idname = "AITK_PT_image_to_3d_panel"
    bl_label = "Image-to-3D"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    def draw(self, context):
        """Render the panel.

        Defensive against a partially-registered addon: when the
        ``Scene.ai_toolkit_image_to_3d`` pointer-property has not been
        attached yet (for example, during a hot reload between
        ``bpy.utils.register_class(AITK_PG_image_to_3d)`` and the
        matching ``Scene.ai_toolkit_image_to_3d = ...`` assignment in
        the addon's top-level ``__init__.register()``), ``getattr``
        returns ``None`` and we render a single explanatory label
        instead of raising -- which would otherwise spam Blender's
        console with a draw-time exception every redraw.
        """
        layout = self.layout
        scene = getattr(context, "scene", None)
        props = getattr(scene, "ai_toolkit_image_to_3d", None)
        if props is None:
            layout.label(
                text="AI Toolkit not fully registered",
                icon="ERROR",
            )
            return

        # 1) File picker. ``subtype='FILE_PATH'`` on the underlying
        #    StringProperty makes Blender render the file-browser
        #    button automatically (Requirement 5.1).
        layout.prop(props, "image_path", text="Image")

        # 2) Hint label -- the actual extension filter is applied by
        #    the submit operator (task 17.2).
        layout.label(
            text="Supported: .png, .jpg, .jpeg, .webp",
            icon="INFO",
        )

        # 3) Thumbnail preview row (Requirement 5.9). We only attempt
        #    a preview when the user has actually selected a file;
        #    rendering nothing on an empty path keeps the panel clean
        #    on first open. The size computation is delegated to
        #    :meth:`_compute_thumbnail_size`, which always exercises
        #    :func:`fit_thumbnail` so the panel never displays a
        #    preview larger than 256 px on either edge -- the explicit
        #    contract Requirement 5.9 locks in.
        if props.image_path:
            thumb_w, thumb_h = self._compute_thumbnail_size(props.image_path)
            if thumb_w > 0 and thumb_h > 0:
                box = layout.box()
                box.label(
                    text=f"Preview: {thumb_w}x{thumb_h} px",
                    icon="IMAGE_DATA",
                )
                # Best-effort: render a path label as a minimal
                # placeholder so the user can confirm what file is
                # selected. The full GPU-backed preview is a
                # follow-up; the contract we lock in here is the
                # 256 px sizing via :func:`fit_thumbnail`.
                box.label(text=props.image_path)
            else:
                layout.label(
                    text="Cannot preview image",
                    icon="ERROR",
                )

        status = props.status

        # 4) Generate row. Disable while a job is already in flight so
        #    the user cannot submit a second job on top of the first.
        submit_row = layout.row()
        submit_row.enabled = status not in _ACTIVE_STATUSES
        submit_row.operator(
            "aitk.submit_image_to_3d",
            text="Generate",
            icon="PLAY",
        )

        # 5) Status label (Requirement 5.8). The 2-second refresh
        #    budget is satisfied because the job dispatcher ticks
        #    every 100 ms and re-tags this area for redraw.
        layout.label(text=f"Status: {status}", icon="INFO")

        # 6) Optional error row (Requirement 5.10).
        if props.failure_reason:
            layout.label(
                text=f"Error: {props.failure_reason}",
                icon="ERROR",
            )

        # 7) Cancel row -- enabled iff the job is in-flight, mirroring
        #    the cancel-enabled rule from the Text-to-3D module for
        #    UX consistency across the five Generation_Modules.
        cancel_row = layout.row()
        cancel_row.enabled = status in _ACTIVE_STATUSES
        cancel_row.operator(
            "aitk.cancel_image_to_3d",
            text="Cancel",
            icon="CANCEL",
        )

    def _compute_thumbnail_size(self, path):
        """Return aspect-preserving thumbnail dimensions for ``path``.

        The image at ``path`` is loaded into ``bpy.data.images`` with
        ``check_existing=True`` (so reloading the same path does not
        duplicate the datablock) and its ``size`` tuple is fed into
        :func:`~ai_toolkit.services.validation.fit_thumbnail` to cap
        the longer edge at 256 px while preserving aspect ratio.

        Returns ``(0, 0)`` on any failure -- file does not exist, the
        loader raises, the image reports a zero-or-negative dimension,
        or :func:`fit_thumbnail` rejects the dimensions. The caller
        renders a "Cannot preview image" label in that case so the
        panel always shows something for the user.

        The method is intentionally an instance method rather than a
        ``@staticmethod`` so the smoke test in this task and any
        future replacement (a real GPU-backed preview) can swap the
        sizing strategy without touching the panel's :meth:`draw`.
        Implements Requirement 5.9 / Property 16.
        """
        try:
            image = bpy.data.images.load(path, check_existing=True)
            # ``image.size`` is a 2-tuple (width, height) of ints in
            # Blender's API. Coerce defensively in case a stub or a
            # mocked image returns floats.
            w = int(image.size[0])
            h = int(image.size[1])
            if w < 1 or h < 1:
                return (0, 0)
            return fit_thumbnail(w, h)
        except Exception:
            logger.debug(
                "Image-to-3D thumbnail load failed for %s",
                path,
                exc_info=True,
            )
            return (0, 0)


__all__ = [
    "AITK_PG_image_to_3d",
    "AITK_OT_show_image_to_3d_panel",
    "AITK_PT_image_to_3d_panel",
]
