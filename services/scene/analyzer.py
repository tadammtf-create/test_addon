
"""Scene analyzer: bpy-free dataclasses and the :class:`SceneAnalyzer`.

This module is part of the **Service Layer** and therefore MUST NOT import any
Blender-distributed module (``bpy``, ``bpy_extras``, ``mathutils``, ``bgl``,
``gpu``, ``bmesh``, ``blf``). Per Requirement 13.1 the entire service layer is
built and tested in a plain Python interpreter; the UI Layer is responsible for
walking the Blender scene and producing the ``SceneSnapshot`` instances this
module consumes.

Three frozen dataclasses are defined here:

* :class:`ObjectSnapshot` -- a single object's stats captured by the UI side
  (task 22.1) so the analyzer can reason about it without touching ``bpy``.
* :class:`SceneSnapshot` -- a whole-scene capture: every object plus the
  scene-level facts (render engine, material count) the analyzer needs.
* :class:`SceneDescription` -- the analyzer's plain-data output. The fields
  satisfy Requirement 9.1 and Requirement 9.2: counts grouped by type,
  active camera presence, render engine, material count, the (capped at 1000)
  selected-object roster, and the ``truncated`` / ``empty_scene`` signal flags
  required by 9.7 / 9.8.

:class:`SceneAnalyzer` lands here too. Its ``describe`` method walks the
snapshot exactly once -- no nested loops -- to satisfy the O(n) performance
bound in Requirement 9.3 (well under 1 second for n < 1000 objects on
commodity hardware). Its ``request_suggestions`` method (task 7.3, Req 9.4 /
9.5) builds a :class:`~ai_toolkit.services.models.requests.SceneAnalysisRequest`
from the description and submits it through an injected ``JobExecutor`` --
unless the scene is empty, in which case it returns a static UI-display
string and does not submit. The module-level :func:`group_suggestions` helper
(task 7.4, Req 9.6) buckets returned suggestions into the canonical five
categories.

To stay out of the ``services.jobs`` import cycle (the executor and handle
modules import from elsewhere in the service layer at module load time and
we'd rather not pull the whole jobs subsystem in just to import the
analyzer) the references to ``JobExecutor`` and ``JobHandle`` are forward
references resolved through ``TYPE_CHECKING``. The
:class:`SceneAnalysisRequest` import is a real runtime import because the
``models`` subpackage is itself bpy-free and free of cycles, so importing it
here is safe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Callable, Final, Optional, TYPE_CHECKING, Union

from ..models.requests import SceneAnalysisRequest

if TYPE_CHECKING:
    # ``JobExecutor`` and ``JobHandle`` are only referenced as type hints in
    # :meth:`SceneAnalyzer.request_suggestions`. Importing them at runtime
    # would force the whole ``services.jobs`` subsystem to load whenever a
    # caller imports ``services.scene.analyzer`` -- needlessly tightening the
    # module graph and creating a potential import cycle if the jobs side
    # ever needs to reference scene types. The forward-reference pattern
    # keeps both sides decoupled.
    from ..jobs.executor import JobExecutor
    from ..jobs.handle import JobHandle


__all__ = [
    "ObjectSnapshot",
    "SceneSnapshot",
    "SceneDescription",
    "SceneAnalyzer",
    "group_suggestions",
    "KNOWN_CATEGORIES",
    "MAX_SELECTED_OBJECTS",
    "MAX_OBJECT_SIZE_BYTES",
    "EMPTY_SCENE_MESSAGE",
]


#: Maximum number of selected objects rolled into ``SceneDescription``
#: (Requirement 9.2). Excess selections are silently dropped at the cap; the
#: ``total_object_count`` still reflects every kept object.
MAX_SELECTED_OBJECTS: Final[int] = 1000

#: Maximum ``serialized_size_bytes`` an :class:`ObjectSnapshot` may declare
#: before the analyzer omits it from the description and sets
#: ``SceneDescription.truncated = True`` (Requirement 9.7). 50 MB.
MAX_OBJECT_SIZE_BYTES: Final[int] = 50 * 1024 * 1024

#: Static UI-display string returned by
#: :meth:`SceneAnalyzer.request_suggestions` when the snapshot is empty
#: (Requirement 9.5). Defined as a module constant so callers and tests can
#: assert against the same literal without re-typing it.
EMPTY_SCENE_MESSAGE: Final[str] = "Add objects to your scene to get suggestions"

#: The five canonical suggestion buckets returned by
#: :func:`group_suggestions` (Requirement 9.6). Order is part of the contract:
#: callers iterating ``output.keys()`` see ``lighting`` first, ``other`` last.
KNOWN_CATEGORIES: Final[tuple[str, ...]] = (
    "lighting",
    "topology",
    "composition",
    "materials",
    "other",
)


@dataclass(frozen=True)
class ObjectSnapshot:
    """A single Blender object's UI-captured snapshot (Req 9.1, 9.2).

    Captured on the UI side from a ``bpy.types.Object`` and handed across the
    UI / Service boundary as plain data so the analyzer can run without
    ``bpy``. The capture itself is implemented in task 22.1.

    Attributes:
        name: Blender's ``object.name``. Used as the canonical object identifier
            across the boundary in keeping with Requirement 13.2 (object names
            cross the boundary, not ``bpy.types.Object`` references).
        object_type: Blender's ``object.type`` string -- one of ``"MESH"``,
            ``"LIGHT"``, ``"CAMERA"``, ``"EMPTY"``, ``"CURVE"``, ``"SURFACE"``,
            ``"META"``, ``"FONT"``, ``"ARMATURE"``, ``"LATTICE"``, ``"GPENCIL"``,
            etc. The analyzer groups counts by this string (Req 9.1).
        light_type: For lights only, Blender's ``light.type`` string -- one of
            ``"POINT"``, ``"SUN"``, ``"SPOT"``, ``"AREA"``. ``None`` for any
            non-light object.
        vertex_count: Mesh vertex count, ``0`` for non-mesh objects (Req 9.2).
        face_count: Mesh polygon count, ``0`` for non-mesh objects (Req 9.2).
        material_names: Names of materials assigned to this object's material
            slots, in slot order. A tuple so the dataclass stays hashable and
            safe to share between threads.
        is_selected: True iff the object was in
            ``bpy.context.selected_objects`` at capture time. The analyzer uses
            this to populate ``SceneDescription.selected_objects`` (Req 9.2).
        is_active_camera: True iff the object is the scene's active camera.
            The analyzer aggregates across all snapshots to derive
            ``SceneDescription.active_camera_present`` (Req 9.1).
        serialized_size_bytes: Approximate byte cost of this snapshot record
            once serialised. Set by the UI-side capture (task 22.1) and used by
            ``SceneAnalyzer.describe`` to omit oversized records (> 50 MB) per
            Requirement 9.7.
    """

    name: str
    object_type: str
    light_type: Optional[str] = None
    vertex_count: int = 0
    face_count: int = 0
    material_names: tuple[str, ...] = ()
    is_selected: bool = False
    is_active_camera: bool = False
    serialized_size_bytes: int = 0


@dataclass(frozen=True)
class SceneSnapshot:
    """A full scene capture handed to ``SceneAnalyzer.describe`` (Req 9.1).

    Attributes:
        objects: Every object in the scene, in iteration order. The analyzer
            walks this once in O(n) -- no nested loops -- to satisfy the
            < 1 second budget for n < 1000 objects (Req 9.3).
        render_engine: ``scene.render.engine``, e.g. ``"BLENDER_EEVEE"``,
            ``"CYCLES"``, ``"BLENDER_WORKBENCH"``.
        material_count: Total distinct materials in ``bpy.data.materials``.
            Captured at the UI layer because the service layer cannot read it.
    """

    objects: tuple[ObjectSnapshot, ...]
    render_engine: str
    material_count: int


@dataclass(frozen=True, kw_only=True)
class SceneDescription:
    """The analyzer's plain-data output (Req 9.1, 9.2, 9.7, 9.8).

    JSON-serialisable: every field is a primitive, a tuple/dict of primitives,
    or a tuple of :class:`ObjectSnapshot` (which is itself primitives only).

    Declared with ``kw_only=True`` because the task-specified field order
    interleaves required fields (``active_camera_present``, ``render_engine``,
    ``material_count``, ``selected_objects``) with defaulted fields
    (``object_counts_by_type``, ``light_counts_by_type``, ``truncated``,
    ``empty_scene``). ``kw_only`` lets us honour that order without violating
    Python's "non-default after default" rule. Callers always construct via
    keyword arguments.

    Attributes:
        total_object_count: Number of objects in the source ``SceneSnapshot``
            (Req 9.1).
        object_counts_by_type: Map from ``object_type`` (e.g. ``"MESH"``) to the
            number of objects of that type (Req 9.1). Defaults to a fresh empty
            dict so the empty-scene case round-trips through JSON cleanly.
        light_counts_by_type: Map from ``light_type`` (e.g. ``"POINT"``) to the
            number of lights of that subtype (Req 9.1). Defaults to a fresh
            empty dict.
        active_camera_present: True iff the snapshot contained at least one
            object with ``is_active_camera == True`` (Req 9.1).
        render_engine: Pass-through from the ``SceneSnapshot`` (Req 9.1).
        material_count: Pass-through from the ``SceneSnapshot`` (Req 9.1).
        selected_objects: Selected-object snapshots, capped at 1000 entries
            per Requirement 9.2.
        truncated: True iff at least one ``ObjectSnapshot`` was omitted because
            its ``serialized_size_bytes`` exceeded 50 MB (Req 9.7).
        empty_scene: True iff ``total_object_count == 0`` (Req 9.8).
    """

    total_object_count: int
    object_counts_by_type: dict[str, int] = field(default_factory=dict)
    light_counts_by_type: dict[str, int] = field(default_factory=dict)
    active_camera_present: bool
    render_engine: str
    material_count: int
    selected_objects: tuple[ObjectSnapshot, ...]
    truncated: bool = False
    empty_scene: bool = False


class SceneAnalyzer:
    """Pure analyser over a :class:`SceneSnapshot`.

    Stateless and bpy-free. All Blender-side capture happens in
    ``ui/scene_capture.py`` (task 22.1); this class only operates on the
    plain-data snapshots that capture produces, satisfying Requirement 13.1.
    See Requirements 9.1 through 9.8 for the contract.
    """

    def describe(self, snapshot: SceneSnapshot) -> SceneDescription:
        """Build a :class:`SceneDescription` from a :class:`SceneSnapshot`.

        Walks ``snapshot.objects`` exactly once, accumulating:

        * ``object_counts_by_type`` and ``light_counts_by_type`` histograms
          (Requirement 9.1).
        * ``active_camera_present`` -- the logical OR of the per-object
          ``is_active_camera`` flags across the snapshot (Requirement 9.1).
        * ``selected_objects`` -- the per-selected-object roster, capped at
          :data:`MAX_SELECTED_OBJECTS` (Requirement 9.2).
        * ``truncated`` -- set when any record was skipped because its
          ``serialized_size_bytes`` exceeded :data:`MAX_OBJECT_SIZE_BYTES`
          (Requirement 9.7). Skipped records do *not* count toward
          ``total_object_count``, the type histograms, the light histograms,
          or the selected list -- their size is the entire reason we are
          dropping them.

        Sets ``empty_scene`` when the resulting ``total_object_count`` is zero
        (Requirement 9.8).

        Implementation is O(n) over ``snapshot.objects`` with no nested loops,
        per Requirement 9.3.
        """
        object_counts: dict[str, int] = {}
        light_counts: dict[str, int] = {}
        selected: list[ObjectSnapshot] = []
        total = 0
        active_camera = False
        truncated = False

        for obj in snapshot.objects:
            if obj.serialized_size_bytes > MAX_OBJECT_SIZE_BYTES:
                # Req 9.7: omit oversized records and flag the description.
                truncated = True
                continue

            total += 1
            object_counts[obj.object_type] = (
                object_counts.get(obj.object_type, 0) + 1
            )

            if obj.object_type == "LIGHT" and obj.light_type:
                light_counts[obj.light_type] = (
                    light_counts.get(obj.light_type, 0) + 1
                )

            if obj.is_active_camera:
                active_camera = True

            if obj.is_selected and len(selected) < MAX_SELECTED_OBJECTS:
                selected.append(obj)

        return SceneDescription(
            total_object_count=total,
            object_counts_by_type=object_counts,
            light_counts_by_type=light_counts,
            active_camera_present=active_camera,
            render_engine=snapshot.render_engine,
            material_count=snapshot.material_count,
            selected_objects=tuple(selected),
            truncated=truncated,
            empty_scene=(total == 0),
        )

    def request_suggestions(
        self,
        snapshot: SceneSnapshot,
        executor: "JobExecutor",
        on_status: Optional[Callable[[str, dict], None]] = None,
    ) -> Union[str, "JobHandle"]:
        """Request workflow suggestions for ``snapshot`` via the job executor.

        Implements Requirements 9.4 and 9.5:

        * For an empty scene (``description.empty_scene == True``) returns
          the literal :data:`EMPTY_SCENE_MESSAGE` and does *not* submit a
          ``scene_analysis`` Generation_Job. The UI layer renders the
          returned string verbatim in the AI_Assistant panel.
        * For a non-empty scene, builds a
          :class:`~ai_toolkit.services.models.requests.SceneAnalysisRequest`
          carrying the analyzer's :class:`SceneDescription` flattened to a
          plain ``dict`` (Req 13.2: every value crossing the UI/service
          boundary is JSON-serialisable), then submits it through
          ``executor`` under the canonical ``"scene_analysis"`` task
          identifier and returns the resulting :class:`JobHandle`.

        :func:`dataclasses.asdict` does the dict conversion in one call:
        :class:`SceneDescription` is a frozen dataclass containing only
        primitives, dicts of primitives, and tuples of frozen
        :class:`ObjectSnapshot` (which is itself primitives only), so
        ``asdict`` recursively yields a JSON-friendly tree. Tuples become
        lists in the dict form -- that is fine at this layer; the request is
        only required to hold a serialisable dict, not preserve container
        types.

        Args:
            snapshot: The :class:`SceneSnapshot` produced by the UI-side
                capture for the active scene.
            executor: The :class:`JobExecutor` singleton owned by the addon.
                Forward-referenced via ``TYPE_CHECKING`` so this module does
                not pull the whole jobs subsystem in at import time.
            on_status: Optional status callback forwarded verbatim to
                :meth:`JobExecutor.submit`. The executor calls it with
                ``(job_id, payload)`` on every status transition (Req 13.7,
                15.7).

        Returns:
            Either the literal :data:`EMPTY_SCENE_MESSAGE` (Req 9.5) or the
            :class:`JobHandle` returned by :meth:`JobExecutor.submit`
            (Req 9.4).
        """
        description = self.describe(snapshot)

        if description.empty_scene:
            # Req 9.5: skip submission entirely and return the UI-display
            # message verbatim. The literal lives in EMPTY_SCENE_MESSAGE so
            # the contract is single-sourced.
            return EMPTY_SCENE_MESSAGE

        # ``asdict`` recursively flattens the frozen dataclass tree into a
        # JSON-friendly dict. Tuples become lists, which is the desired
        # shape at the request boundary (Req 13.2).
        request = SceneAnalysisRequest(scene_description=asdict(description))

        # Req 9.4: submit under the canonical task identifier and return the
        # executor's handle to the caller (typically the AI_Assistant panel).
        return executor.submit(
            "scene_analysis",
            request,
            on_status=on_status,
        )


def group_suggestions(
    suggestions: Union[
        Sequence[Union[str, tuple[str, str]]],
        Mapping[str, str],
    ],
) -> dict[str, list[str]]:
    """Bucket suggestions into the canonical five categories (Req 9.6).

    Output is **always** a ``dict`` whose keys are exactly
    :data:`KNOWN_CATEGORIES` -- ``lighting``, ``topology``, ``composition``,
    ``materials``, ``other`` -- in that registration order. Each value is a
    list of suggestion strings; categories with no suggestions get an empty
    list, which lets the UI render every section header even when a bucket
    is empty.

    Three input shapes are accepted so callers can use whichever shape their
    upstream data already has:

    1. **Iterable of ``(category, suggestion)`` tuples.** Bucketed by
       ``category.lower()``; unknown categories fall to ``"other"``.
    2. **Iterable of plain strings.** Every entry goes under ``"other"``.
       This is the "no category hints available" path and matches the
       Req 9.6 mandate that uncategorised suggestions land in ``other``.
    3. **Mapping ``{suggestion -> category}``.** Matches the shape of
       :attr:`SceneAnalysisResponse.category_hints`
       (``services/models/responses.py``). Iterated via ``.items()`` and
       bucketed using the same case-insensitive matching rule as shape 1.

    Mapping vs iterable is detected via ``isinstance(suggestions, Mapping)``
    from :mod:`collections.abc` so any ``dict``-like object (including
    ``OrderedDict``, ``MappingProxyType`` and similar standard-library
    mapping wrappers) is recognised. Inside an iterable, items are tested
    with ``isinstance(item, str)`` first so a string is never mistakenly
    unpacked as a 2-tuple of single-character category and suggestion --
    Python's tuple-unpacking would happily destructure ``"hi"`` into
    ``("h", "i")`` otherwise.

    Args:
        suggestions: Input suggestions in one of the three shapes above.

    Returns:
        A fresh ``dict[str, list[str]]`` keyed by exactly the five canonical
        categories, in :data:`KNOWN_CATEGORIES` order, with suggestions
        appended in input order within each bucket.
    """
    # Initialise with every canonical key in the documented order so the
    # output's ``.keys()`` iteration order matches KNOWN_CATEGORIES exactly.
    # Python dicts preserve insertion order (3.7+), so this is the contract.
    result: dict[str, list[str]] = {category: [] for category in KNOWN_CATEGORIES}

    def _bucket_for(raw_category: str) -> str:
        """Normalise ``raw_category`` to one of the canonical bucket keys.

        Performs case-insensitive matching against :data:`KNOWN_CATEGORIES`
        (so ``"Topology"``, ``"TOPOLOGY"``, and ``"topology"`` all land in
        the ``topology`` bucket) and falls back to ``"other"`` for any
        category that does not match a known bucket -- including the
        ``"other"`` value itself, which simply round-trips.
        """
        normalised = raw_category.lower()
        if normalised in result:
            return normalised
        return "other"

    if isinstance(suggestions, Mapping):
        # Shape 3: {suggestion -> category}. Matches
        # SceneAnalysisResponse.category_hints exactly.
        for suggestion, category in suggestions.items():
            result[_bucket_for(str(category))].append(str(suggestion))
        return result

    # Shapes 1 and 2: iterable of either strings or (category, suggestion)
    # tuples. Decide per item -- a heterogeneous iterable would still be
    # handled correctly, though no caller is expected to mix the two.
    for item in suggestions:
        if isinstance(item, str):
            # Shape 2: bare string -> "other" (Req 9.6's default-when-no-hint
            # rule applies even when the input has no category info at all).
            result["other"].append(item)
        else:
            # Shape 1: (category, suggestion) tuple. Unpack defensively --
            # any 2-element iterable works, but tuples are the documented
            # shape.
            category, suggestion = item
            result[_bucket_for(str(category))].append(str(suggestion))

    return result
