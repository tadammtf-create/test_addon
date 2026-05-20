"""AI Texturing PropertyGroup, panel, toggle operator, and 'Show panel' operator.

The toggle helper itself (``services.texturing.toggle_texture_map``)
is bpy-free and delegated to from this module's toggle operator. The
submit operator (``AITK_OT_submit_texture_generation``) and the cancel
operator (``AITK_OT_cancel_texture_generation``) live in
``ui/operators/job_ops.py`` and land in task 18.3.

Per Requirement 6.1 the prompt input accepts up to 1000 characters and
per Requirement 6.2 the four texture-map kinds (``base_color``,
``normal``, ``roughness``, ``metallic``) are surfaced as a multi-select
where ``base_color`` is selected by default and at least one option
must remain selected at all times. The "at least one" invariant is
enforced by routing every checkbox click through
:class:`AITK_OT_toggle_texture_map` rather than letting the user mutate
the underlying :class:`bpy.props.BoolProperty` directly: a real
checkbox would let the user clear every option in sequence and break
the invariant, but an operator can refuse the toggle and emit a
warning. This mirrors the
:func:`~ai_toolkit.services.texturing.toggle_texture_map` contract,
which returns a structured rejection rather than raising — the bpy-free
property test (task 18.4 / Property 17) drives the same helper through
arbitrary toggle sequences and asserts the invariant.

Validates: Requirements 6.1, 6.2.

.. note::
    This module deliberately omits ``from __future__ import annotations``.
    PEP 563 string-stringifies all class-level annotations, which would
    leave :class:`AITK_PG_texturing`'s
    ``prompt: bpy.props.StringProperty(...)`` and per-flag
    ``map_*: bpy.props.BoolProperty(...)`` declarations as plain
    strings in ``__annotations__`` instead of executing the
    :mod:`bpy.props` calls at class-definition time. Blender's
    :class:`bpy.types.PropertyGroup` registration walks
    ``__annotations__`` expecting the real property descriptors, so
    enabling PEP 563 here would silently break the addon's per-scene
    state. The sibling ``text_to_3d_panel.py`` follows the same
    convention.
"""

import logging

import bpy

from ...services.texturing import (
    DEFAULT_TEXTURE_MAPS,
    TEXTURE_MAP_KINDS,
    toggle_texture_map,
)


logger = logging.getLogger("ai_toolkit")


# Mapping between the textbook kind strings used by the service layer
# and the per-flag :class:`bpy.props.BoolProperty` attributes that back
# them on the :class:`AITK_PG_texturing` PropertyGroup. Declaring the
# mapping once at module level keeps the toggle operator and the panel
# draw method in lock-step: any future kind addition is a single-line
# change here plus the matching ``BoolProperty`` declaration.
_MAP_KIND_TO_ATTR = {
    "base_color": "map_base_color",
    "normal": "map_normal",
    "roughness": "map_roughness",
    "metallic": "map_metallic",
}

# Display labels for the texture-map toggle buttons in the panel. Kept
# adjacent to ``_MAP_KIND_TO_ATTR`` so the four kinds are described in
# one place.
_MAP_KIND_LABELS = {
    "base_color": "Base Color",
    "normal": "Normal",
    "roughness": "Roughness",
    "metallic": "Metallic",
}


def _read_selected_set(props) -> set:
    """Return the set of currently-selected map-kind strings from ``props``.

    The :class:`bpy.props.BoolProperty` flags on
    :class:`AITK_PG_texturing` are the single source of truth for the
    multi-select; this helper translates those four booleans into the
    plain :class:`set` that
    :func:`~ai_toolkit.services.texturing.toggle_texture_map` operates
    on, isolating the bpy-side encoding from the bpy-free helper.
    """
    selected = set()
    for kind, attr in _MAP_KIND_TO_ATTR.items():
        if getattr(props, attr, False):
            selected.add(kind)
    return selected


def _write_selected_set(props, selected) -> None:
    """Write the resulting selected set back to the four BoolProperty flags.

    Counterpart to :func:`_read_selected_set`. ``selected`` is any
    iterable of kind strings — typically a :class:`frozenset` from
    :class:`~ai_toolkit.services.texturing.TextureMapToggleResult`.
    """
    selected_set = set(selected)
    for kind, attr in _MAP_KIND_TO_ATTR.items():
        setattr(props, attr, kind in selected_set)


class AITK_PG_texturing(bpy.types.PropertyGroup):
    """AI Texturing module state stored on :class:`bpy.types.Scene`.

    Lives at ``scene.ai_toolkit_texturing`` (registered by task 24.1)
    so the prompt text, current selection, and active job state
    survive panel redraws and round-trip with the saved .blend file.

    Backing the multi-select with four separate
    :class:`bpy.props.BoolProperty` flags rather than a single
    :class:`bpy.props.EnumProperty` (with ``options={'ENUM_FLAG'}``) is
    a deliberate choice: the per-flag layout makes it trivial to
    inspect the current set in property tests and to render the
    toggle buttons individually with operator-driven dispatch
    (Requirement 6.2).
    """

    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Description of the desired material (max 1000 characters)",
        default="",
        maxlen=1000,
    )

    # The four map-kind flags. ``base_color`` is selected by default
    # per Requirement 6.2 ; the other three default to off. The
    # at-least-one invariant is enforced by
    # :class:`AITK_OT_toggle_texture_map`, not by these defaults — the
    # defaults are only the initial state on first scene render.
    map_base_color: bpy.props.BoolProperty(
        name="Base Color",
        description="Generate the base color (albedo) texture map",
        default=True,
    )
    map_normal: bpy.props.BoolProperty(
        name="Normal",
        description="Generate the normal texture map",
        default=False,
    )
    map_roughness: bpy.props.BoolProperty(
        name="Roughness",
        description="Generate the roughness texture map",
        default=False,
    )
    map_metallic: bpy.props.BoolProperty(
        name="Metallic",
        description="Generate the metallic texture map",
        default=False,
    )

    # Job-state mirror fields populated by the submit operator and the
    # status callback (task 18.3). Stored on the PropertyGroup so the
    # panel ``draw`` method can read them without a side channel.
    status: bpy.props.StringProperty(
        name="Status",
        description="Current AI Texturing job status",
        default="idle",
    )
    failure_reason: bpy.props.StringProperty(
        name="Failure",
        description="Reason for the most recent failure, if any",
        default="",
    )
    current_job_id: bpy.props.StringProperty(
        name="Job",
        description="UUID of the currently-active AI Texturing job",
        default="",
    )


class AITK_OT_toggle_texture_map(bpy.types.Operator):
    """Toggle one texture-map kind, refusing toggles that would empty the set.

    Routing every checkbox click through this operator is what enforces
    Requirement 6.2's "at least one option SHALL remain selected at all
    times" invariant: a real :class:`bpy.props.BoolProperty` checkbox
    would let the user clear every flag in sequence, whereas this
    operator delegates to the bpy-free
    :func:`~ai_toolkit.services.texturing.toggle_texture_map` and
    surfaces the rejection as a Blender warning report.

    The ``map_kind`` operator property is the kind string the user
    clicked; the panel ``draw`` method assigns it on each
    ``layout.operator(...)`` return value.
    """

    bl_idname = "aitk.toggle_texture_map"
    bl_label = "Toggle Texture Map"
    bl_options = {"INTERNAL"}

    map_kind: bpy.props.StringProperty(
        name="Map Kind",
        description="One of base_color, normal, roughness, metallic",
        default="",
    )

    def execute(self, context):
        """Apply the toggle, writing back the result on success.

        Reads the four BoolProperty flags into a plain set, calls the
        pure helper, and either:

        * On rejection: emits a ``WARNING`` report containing the
          helper's reason and returns ``{'CANCELLED'}`` without
          touching the flags. This covers both the empty-set
          rejection (Requirement 6.2) and the "unknown kind" guard
          the helper applies to defensive callers.
        * On acceptance: writes the resulting set back to the flags
          via :func:`_write_selected_set` and returns ``{'FINISHED'}``.
        """
        props = getattr(context.scene, "ai_toolkit_texturing", None)
        if props is None:
            # The scene PropertyGroup is registered in task 24.1; if a
            # click somehow lands before registration completes, fail
            # loud rather than silently swallow the event.
            self.report({"WARNING"}, "AI Toolkit not fully registered")
            return {"CANCELLED"}

        current_set = _read_selected_set(props)
        result = toggle_texture_map(current_set, self.map_kind)

        if result.rejected:
            # The helper's ``reason`` is short and user-readable
            # ("at least one texture map must remain selected" /
            # "unknown texture-map kind 'foo'"), so we surface it
            # verbatim via Blender's report mechanism.
            self.report({"WARNING"}, result.reason)
            return {"CANCELLED"}

        _write_selected_set(props, result.selected)
        return {"FINISHED"}


class AITK_OT_show_texturing_panel(bpy.types.Operator):
    """Bring the AI Texturing panel to the user's attention.

    Wired to the launcher menu's ``AI Texturing`` entry (see
    ``MODULE_REGISTRY`` in ``ui/launcher_menu.py``); invoking this
    operator is the contract that lets the menu open the module
    (Requirement 2.3). The panel itself lives in the N-panel sidebar
    under the ``AI Toolkit`` category so it is always reachable; this
    operator's job is to nudge the active area to redraw so the panel
    is rendered on the next frame even if Blender was lazy-skipping
    the sidebar.

    The launcher menu invokes this with ``INVOKE_DEFAULT``; a richer
    popover-style implementation may follow per the design's
    ``draw_handler_add`` pattern, but a tag-redraw return is
    sufficient for the initial release.
    """

    bl_idname = "aitk.show_texturing_panel"
    bl_label = "Show AI Texturing"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        """Tag the active area for redraw and return ``{'FINISHED'}``.

        Defensive: ``context.area`` is ``None`` when the operator is
        invoked from a context without an area (the F3 search, the
        Python console). In that case there is nothing to redraw, but
        the operator still completes so the launcher menu does not
        report a failure.
        """
        area = getattr(context, "area", None)
        if area is not None:
            try:
                area.tag_redraw()
            except Exception:
                # Defensive: a stale area handle during a hot reload
                # should never crash the launcher dispatch path.
                logger.debug(
                    "AITK_OT_show_texturing_panel: tag_redraw failed",
                    exc_info=True,
                )
        return {"FINISHED"}


class AITK_PT_texturing_panel(bpy.types.Panel):
    """N-panel sidebar panel for the AI Texturing Generation Module.

    Renders, top to bottom:

    1. The prompt input (Requirement 6.1).
    2. The four texture-map toggle buttons (Requirement 6.2). The
       buttons are ``layout.operator(...)`` calls dispatching to
       :class:`AITK_OT_toggle_texture_map` rather than
       ``layout.prop(props, "map_*")`` checkboxes — the former path
       enforces the at-least-one invariant on every click; the latter
       would let the user clear every flag.
    3. The submit ``Generate`` button, enabled only when no job is
       active (the four non-terminal-or-idle statuses come from task
       18.3).
    4. A status line and an optional inline error line populated by
       the submit operator's status callback.
    5. The cancel button, enabled iff the job is in ``queued`` or
       ``running`` state.
    """

    bl_idname = "AITK_PT_texturing_panel"
    bl_label = "AI Texturing"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    def draw(self, context):
        layout = self.layout
        props = getattr(context.scene, "ai_toolkit_texturing", None)
        if props is None:
            # Same defensive branch as the toggle operator: the
            # PropertyGroup pointer is registered in task 24.1 and is
            # absent until then. Render a clear hint instead of a
            # traceback so partial registration is debuggable.
            layout.label(text="AI Toolkit not fully registered", icon="ERROR")
            return

        # 1) Prompt input (Requirement 6.1).
        layout.prop(props, "prompt", text="Prompt")

        # 2) Texture-map multi-select. We render each kind as an
        # operator button (``layout.operator``) rather than a
        # ``layout.prop`` checkbox so every click goes through
        # :class:`AITK_OT_toggle_texture_map`, which refuses toggles
        # that would empty the set (Requirement 6.2). The ``depress``
        # argument renders the button "pressed in" when the
        # corresponding flag is set, mimicking a checkbox visually
        # while keeping the operator path on every click.
        layout.label(text="Texture maps:")
        grid = layout.grid_flow(row_major=True, columns=2, align=True)
        for kind, attr in _MAP_KIND_TO_ATTR.items():
            label = _MAP_KIND_LABELS[kind]
            is_on = bool(getattr(props, attr, False))
            op = grid.operator(
                "aitk.toggle_texture_map",
                text=label,
                depress=is_on,
                icon="CHECKBOX_HLT" if is_on else "CHECKBOX_DEHLT",
            )
            op.map_kind = kind

        # 3) Submit button (operator from task 18.3). Enabled only
        # when the previous job has reached a terminal state, or when
        # no job has run yet (``idle``). The submit operator is not
        # registered yet during incremental development, but
        # ``layout.operator`` accepts an unregistered bl_idname and
        # renders a disabled "missing" button rather than raising —
        # so the panel still draws cleanly.
        status = props.status
        row = layout.row()
        row.enabled = status in ("idle", "succeeded", "failed", "cancelled")
        row.operator(
            "aitk.submit_texture_generation", text="Generate", icon="PLAY"
        )

        # 4) Status / error display. The status string is one of the
        # five labels driven by the executor's status callbacks
        # (Requirement 6.5 / task 18.3) plus the local ``idle``
        # bootstrap; the ``failure_reason`` line is populated only on
        # terminal ``failed`` so a successful or in-progress job
        # shows just the status line.
        layout.label(text=f"Status: {status}", icon="INFO")
        if props.failure_reason:
            layout.label(
                text=f"Error: {props.failure_reason}", icon="ERROR"
            )

        # 5) Cancel control (operator from task 18.3). Only enabled
        # for the two pre-terminal statuses per Requirement 4.6 /
        # 6.x ; the same enablement contract is reused here.
        cancel_row = layout.row()
        cancel_row.enabled = status in ("queued", "running")
        cancel_row.operator(
            "aitk.cancel_texture_generation", text="Cancel", icon="CANCEL"
        )


__all__ = [
    "AITK_PG_texturing",
    "AITK_OT_toggle_texture_map",
    "AITK_OT_show_texturing_panel",
    "AITK_PT_texturing_panel",
]
