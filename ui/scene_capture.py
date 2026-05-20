"""UI-side scene capture: walks bpy.context.scene to build a SceneSnapshot.

This is the *only* place the SceneSnapshot dataclass is constructed --
tests construct snapshots directly, of course, but in production the
snapshot is captured here from a live Blender scene and handed to the
bpy-free SceneAnalyzer (services/scene/analyzer.py) for analysis. The
analyzer itself never imports bpy; this module imports bpy because it
must walk the scene.

The capture is deliberately defensive. The AI Assistant's "Include scene
context" toggle (Requirement 8.7) means this code can run on every chat
submission while the user is still actively editing the scene -- a
half-loaded mesh, a depsgraph in mid-update, or a quick teardown race
during an addon reload can all turn ordinary attribute access into an
exception. Per-field accessors are therefore wrapped individually so a
single bad object never knocks out the rest of the snapshot, and the
top-level body is wrapped so that any *unexpected* failure logs and
returns an empty snapshot rather than crashes the chat panel.

Per-object capture follows the rules in task 22.1 / Requirements 9.1, 9.2:

* Vertex and face counts are read from ``obj.data.vertices`` and
  ``obj.data.polygons`` respectively. ``bmesh`` is explicitly NOT used --
  ``bmesh.from_edit_mesh`` would require an active edit-mode mesh and a
  ``bmesh.new()`` round-trip is far too expensive for a per-chat-message
  capture. The direct length read is O(1) for a stored mesh.
* Material slot names are flattened to a tuple of strings -- never the
  ``bpy.types.Material`` references themselves -- so the resulting
  ObjectSnapshot stays plain-data and safe to cross the UI / Service
  boundary (Requirement 13.2).
* Selection is read from the view layer (``bpy.context.view_layer.objects.
  selected``) when available because selection is per-view-layer in
  Blender 2.8+. ``select_get()`` on each scene object is the documented
  fallback when no view layer is wired up (e.g. during ``unregister``).
* The ``serialized_size_bytes`` field is a coarse approximation -- the
  analyzer only consults it to drop > 50 MB records (Requirement 9.7),
  which a normal in-Blender capture never approaches. Tests that need to
  exercise the truncation branch construct ObjectSnapshots directly with
  explicit large sizes.

Returning an empty snapshot rather than ``None`` is intentional: it keeps
``SceneAnalyzer.describe`` total -- it has a defined behaviour for every
input -- and lets the AI Assistant render the static empty-scene message
through the same code path it uses for "actually no objects" without
having to handle a separate "capture failed" case.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import bpy

from ..services.scene.analyzer import (
    ObjectSnapshot,
    SceneSnapshot,
)


__all__ = ["collect_scene_snapshot"]


# Single-sourced addon-wide logger. Configured in ``services/__init__.py``;
# every UI-layer module fetches the same logger by name so log records
# share one namespace.
logger = logging.getLogger("ai_toolkit")


# Defensive bail-out value, used by every "could not capture" path:
# missing scene during a teardown race, unexpected exception during the
# walk, etc. Single-sourced so the contract is one literal.
_EMPTY_SNAPSHOT: SceneSnapshot = SceneSnapshot(
    objects=(),
    render_engine="",
    material_count=0,
)


def collect_scene_snapshot(
    scene: Optional["bpy.types.Scene"] = None,
) -> SceneSnapshot:
    """Walk the active scene and return a SceneSnapshot.

    Args:
        scene: Optional explicit Blender scene. Defaults to
            ``bpy.context.scene``.

    Returns:
        A SceneSnapshot whose ``objects`` tuple contains one
        ObjectSnapshot per object in ``scene.objects``. Per-object
        capture is best-effort: an object whose data block can't be
        introspected (e.g. due to a corrupt mesh) is captured with
        zero vertex/face counts and an empty material list, NOT
        omitted, so the analyzer's ``total_object_count`` stays
        accurate.

    Notes:
        * Returns an empty SceneSnapshot if ``bpy.context.scene`` is
          ``None`` (teardown race) or if any unexpected exception
          escapes the body. This guarantees the AI Assistant's
          "Include scene context" toggle (Req 8.7) never crashes the
          addon.
        * The active camera is identified by **name**, not object
          identity, so the comparison is robust to bpy returning fresh
          wrapper objects on successive attribute accesses.
    """
    try:
        if scene is None:
            scene = bpy.context.scene
            if scene is None:
                # ``bpy.context.scene`` can briefly be ``None`` during
                # an addon ``unregister`` or while a new file is being
                # loaded. Bail out with the documented empty snapshot
                # rather than raise.
                return _EMPTY_SNAPSHOT

        active_camera_name = _resolve_active_camera_name(scene)
        selected_names = _resolve_selected_names(scene)

        snapshots: list[ObjectSnapshot] = [
            _capture_object(obj, selected_names, active_camera_name)
            for obj in scene.objects
        ]

        material_count = _resolve_material_count()
        render_engine = _resolve_render_engine(scene)

        return SceneSnapshot(
            objects=tuple(snapshots),
            render_engine=render_engine,
            material_count=material_count,
        )
    except Exception:
        # Last-resort guard. Anything that escaped the per-field
        # try/except blocks below is a programming error or a bpy edge
        # case we haven't seen yet -- log it (so we have a diagnostic
        # trail in the system console) and return the documented empty
        # snapshot so the calling UI never crashes the addon.
        logger.exception(
            "collect_scene_snapshot failed; returning empty snapshot"
        )
        return _EMPTY_SNAPSHOT


def _resolve_active_camera_name(scene: "bpy.types.Scene") -> str:
    """Return ``scene.camera.name`` or ``""`` when no active camera is set.

    Identifying the active camera by NAME (rather than holding the
    ``bpy.types.Object`` reference) keeps the comparison robust: bpy
    occasionally returns fresh wrapper objects on successive attribute
    accesses, so two reads of ``scene.camera`` may not be ``is``-equal
    even when they refer to the same scene-level camera. Names are
    stable for the lifetime of the snapshot.
    """
    try:
        cam = scene.camera
        if cam is None:
            return ""
        return cam.name
    except Exception:
        return ""


def _resolve_selected_names(scene: "bpy.types.Scene") -> set[str]:
    """Return the set of names of currently-selected objects.

    Selection is **per-view-layer** in Blender 2.8+, not per-scene, so
    ``scene.objects`` itself can't tell us what's selected. Three
    layered strategies, each guarded:

    1. ``bpy.context.view_layer.objects.selected`` -- the canonical
       Blender 2.8+ accessor; usually fastest because Blender keeps it
       cached.
    2. Iterate ``scene.collection.all_objects`` and call ``select_get()``
       on each. ``select_get()`` is unavailable in some contexts
       (background threads, depsgraph callbacks) so each call is
       individually guarded.
    3. Empty set. Selection is non-essential for the analyzer's
       totals; over-reporting "no selection" is the safe default.
    """
    try:
        view_layer = bpy.context.view_layer
        if view_layer is not None:
            return {obj.name for obj in view_layer.objects.selected}
    except Exception:
        # View-layer access failed (no active window/area, depsgraph
        # mid-update). Fall through to the per-object select_get path.
        pass

    try:
        return {
            obj.name
            for obj in scene.collection.all_objects
            if _safe_select_get(obj)
        }
    except Exception:
        return set()


def _safe_select_get(obj: "bpy.types.Object") -> bool:
    """Call ``obj.select_get()`` defensively.

    ``select_get()`` raises ``RuntimeError`` when called outside an
    active view-layer context (for example during a depsgraph update or
    from a background thread). Treat any failure as "not selected" --
    over-reporting "no selection" is harmless to the analyzer.
    """
    try:
        return bool(obj.select_get())
    except Exception:
        return False


def _resolve_material_count() -> int:
    """Return ``len(bpy.data.materials)`` or 0 when access fails."""
    try:
        return len(bpy.data.materials)
    except Exception:
        return 0


def _resolve_render_engine(scene: "bpy.types.Scene") -> str:
    """Return ``scene.render.engine`` or ``""`` when render access fails."""
    try:
        render = scene.render
        if render is None:
            return ""
        return render.engine
    except Exception:
        return ""


def _capture_object(
    obj: "bpy.types.Object",
    selected_names: set[str],
    active_camera_name: str,
) -> ObjectSnapshot:
    """Capture a single Blender Object into an :class:`ObjectSnapshot`.

    Best-effort: any per-field exception falls back to a safe default
    rather than aborting the whole capture. A corrupt mesh therefore
    contributes a snapshot with zero vertex/face counts but is **not**
    omitted, so ``SceneAnalyzer.describe`` still counts it toward
    ``total_object_count`` and ``object_counts_by_type`` (Req 9.1).
    """
    name = obj.name
    object_type = obj.type

    light_type = _capture_light_type(obj, object_type)
    vertex_count, face_count = _capture_mesh_counts(obj, object_type)
    material_names = _capture_material_names(obj)

    is_selected = name in selected_names
    # The active-camera test combines BOTH the name match and the type
    # check. The type check is what disambiguates the (unlikely but
    # legal) case where a non-camera object shares its name with the
    # active camera after a rename round-trip.
    is_active_camera = (
        object_type == "CAMERA" and name == active_camera_name
    )

    # Coarse byte-cost approximation. The analyzer only consults this
    # for the > 50 MB truncation cut-off (Req 9.7), which an in-Blender
    # capture never approaches -- a real ObjectSnapshot is at most a
    # few hundred bytes of metadata. Tests that exercise the truncation
    # branch construct ObjectSnapshots directly with explicit large
    # sizes; this formula merely guarantees the field is always
    # populated with a non-zero value.
    serialized_size_bytes = (
        sys.getsizeof(name) + 16 * len(material_names) + 32
    )

    return ObjectSnapshot(
        name=name,
        object_type=object_type,
        light_type=light_type,
        vertex_count=vertex_count,
        face_count=face_count,
        material_names=material_names,
        is_selected=is_selected,
        is_active_camera=is_active_camera,
        serialized_size_bytes=serialized_size_bytes,
    )


def _capture_light_type(
    obj: "bpy.types.Object",
    object_type: str,
) -> Optional[str]:
    """Return the ``light.type`` string for LIGHT objects, else ``None``.

    Defensive against ``obj.data is None`` (a light without a data
    block, which Blender will sometimes show during file load) and
    against the data block lacking a ``type`` attribute (some
    third-party light types).
    """
    if object_type != "LIGHT":
        return None
    try:
        data = obj.data
        if data is not None and hasattr(data, "type"):
            return data.type
    except Exception:
        return None
    return None


def _capture_mesh_counts(
    obj: "bpy.types.Object",
    object_type: str,
) -> tuple[int, int]:
    """Return ``(vertex_count, face_count)`` for MESH objects, else ``(0, 0)``.

    Vertex / face counts are read directly from ``obj.data.vertices``
    and ``obj.data.polygons`` -- the task spec is explicit that
    ``bmesh`` MUST NOT be used here. ``len()`` on a stored mesh's
    vertex/polygon collection is O(1).

    The two counts are read in independent ``try`` blocks so a corrupt
    vertices collection doesn't suppress an otherwise-readable polygon
    collection (or vice versa).
    """
    if object_type != "MESH":
        return 0, 0

    try:
        data = obj.data
    except Exception:
        return 0, 0
    if data is None:
        return 0, 0

    try:
        vertex_count = len(data.vertices)
    except Exception:
        vertex_count = 0

    try:
        face_count = len(data.polygons)
    except Exception:
        face_count = 0

    return vertex_count, face_count


def _capture_material_names(obj: "bpy.types.Object") -> tuple[str, ...]:
    """Return slot-order tuple of material names, empty tuple on any failure.

    The result is a tuple (not a list) so the parent
    :class:`ObjectSnapshot` stays hashable and is safe to share between
    threads. ``slot.material`` may legitimately be ``None`` for an
    empty slot -- those are filtered out so the tuple contains only
    actual material names.
    """
    try:
        return tuple(
            slot.material.name
            for slot in obj.material_slots
            if slot.material is not None
        )
    except Exception:
        return ()
