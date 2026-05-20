"""Submit/cancel/send/save operators for every Generation_Module.

This is the **UI Layer** glue that turns Blender operator clicks into
``JobExecutor`` submissions and routes status events back into the
panels' :class:`bpy.types.PropertyGroup` mirror fields.

The bpy-free service layer (``JobExecutor``, ``ProviderRegistry``,
validation helpers, request dataclasses) does the heavy lifting; this
module only:

* Builds the request dataclass from the active scene's PropertyGroup
  fields.
* Submits to :class:`~ai_toolkit.services.jobs.executor.JobExecutor`
  with an ``on_status`` callback that re-routes every status event onto
  the main thread via the
  :class:`~ai_toolkit.services.jobs.callbacks.CallbackQueue`.
* Updates the PropertyGroup's ``status``, ``failure_reason``, and
  ``current_job_id`` fields on every transition so the panel renders
  the correct state.
* Dispatches the resulting file path(s) to the
  :class:`~ai_toolkit.ui.asset_importer.AssetImporter` when the job
  succeeds.
* Cancels the job by calling :meth:`JobHandle.cancel`.

Operator ↔ Task map (one operator class, one task except for the chat
operators which share state):

============================================  =====================
Task                                          Operator class
============================================  =====================
16.2 Text-to-3D submit                        AITK_OT_submit_text_to_3d
16.2 Text-to-3D cancel                        AITK_OT_cancel_text_to_3d
17.2 Image-to-3D submit                       AITK_OT_submit_image_to_3d
17.2 Image-to-3D cancel                       AITK_OT_cancel_image_to_3d
18.3 AI Texturing submit                      AITK_OT_submit_texture_generation
18.3 AI Texturing cancel                      AITK_OT_cancel_texture_generation
19.2 Render Preview submit                    AITK_OT_submit_render_preview
19.2 Render Preview cancel                    AITK_OT_cancel_render_preview
19.3 Save Preview                             AITK_OT_save_preview
20.2 AI Assistant Send                        AITK_OT_send_chat_message
20.2 AI Assistant Cancel                      AITK_OT_cancel_chat_message
20.3 AI Assistant Retry                       AITK_OT_retry_chat_message
20.3 AI Assistant New Conversation            AITK_OT_new_conversation
============================================  =====================

Module-level dependency binding
-------------------------------
The addon's top-level ``__init__.register()`` (task 24.1) is the single
caller of :func:`bind_dependencies`, which attaches the executor, the
callback queue, the asset importer, and the history manager. Operators
read the references via module-level slots rather than walking through
``bpy.context.preferences`` on every click; the slots are ``None`` until
``bind_dependencies`` is called and back to ``None`` after a hot reload
before re-bind. Every operator's ``execute`` checks for ``None`` first
and reports a transient "AI Toolkit is not yet ready" error rather than
crashing.

JobHandle bookkeeping
---------------------
:class:`bpy.types.PropertyGroup` only stores Blender-native property
types, so a live :class:`JobHandle` cannot be stashed there. Instead the
PropertyGroup persists the string ``current_job_id`` and we keep the
:class:`JobHandle` instances in a module-level dict
(:data:`_handles`) keyed by job id. Cancel operators look up the live
handle from this dict; the dict is cleared lazily as terminal status
events come in.
"""

from __future__ import annotations

import datetime
import imghdr
import logging
import os
import shutil
import tempfile
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional

import bpy

from ...services.chat import build_chat_request
from ...services.jobs.callbacks import CallbackQueue
from ...services.jobs.executor import JobExecutor, JobQueueFullError
from ...services.jobs.handle import JobHandle
from ...services.models.requests import (
    ChatMessage,
    ImageTo3DRequest,
    RenderPreviewRequest,
    TextTo3DRequest,
    TextureGenerationRequest,
)
from ...services.scene.analyzer import SceneAnalyzer
from ...services.validation import validate_text
from ..asset_importer import AssetImporter, ImportError_
from ..scene_capture import collect_scene_snapshot


# Single addon-wide logger; same handle the service layer uses.
logger = logging.getLogger("ai_toolkit")


# ---------------------------------------------------------------------------
# Module-level dependency slots, populated by bind_dependencies().
# ---------------------------------------------------------------------------

_executor: Optional[JobExecutor] = None
_callbacks: Optional[CallbackQueue] = None
_asset_importer: Optional[AssetImporter] = None

# Use ``Any`` rather than the concrete ``HistoryManager`` import so this
# module stays importable even if the history subpackage is reshuffled.
# The contract is just "exposes archive_conversation(messages)".
_history_manager: Optional[Any] = None


def bind_dependencies(
    executor: JobExecutor,
    callbacks: CallbackQueue,
    asset_importer: AssetImporter,
    history_manager: Optional[Any] = None,
) -> None:
    """Bind every dependency the operators consult. Idempotent.

    Called by the addon's top-level ``__init__.register()`` (task 24.1)
    once the four singletons have been constructed and before any
    operator is registered. A second call replaces the references,
    which is the desired behaviour after a hot reload.
    """
    global _executor, _callbacks, _asset_importer, _history_manager
    _executor = executor
    _callbacks = callbacks
    _asset_importer = asset_importer
    _history_manager = history_manager


# ---------------------------------------------------------------------------
# Live JobHandle registry (job_id -> JobHandle).
# ---------------------------------------------------------------------------
#
# PropertyGroup fields can hold the job id (a string) but not the live
# JobHandle, so submit operators stash the handle here and cancel
# operators look it up by id. Entries are cleared on terminal status
# events so the dict cannot grow unboundedly across a long Blender
# session.
_handles: Dict[str, JobHandle] = {}


# ---------------------------------------------------------------------------
# Constants used by multiple submit operators.
# ---------------------------------------------------------------------------

#: Image-to-3D file picker accepts only these extensions (Req 5.1, 5.4).
_IMAGE_TO_3D_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

#: Image-to-3D maximum upload size in bytes (Req 5.4): 20 megabytes.
_IMAGE_TO_3D_MAX_BYTES = 20 * 1024 * 1024

#: Texture-map kinds in the canonical order used to build
#: ``TextureGenerationRequest.texture_maps`` and to fall back to a
#: positional ``texture_files`` mapping when the provider's metadata
#: does not supply one.
_TEXTURE_MAP_KIND_ORDER = ("base_color", "normal", "roughness", "metallic")

#: Map a texture-map kind back to the BoolProperty attribute that
#: backs it on :class:`AITK_PG_texturing`. Kept in sync with
#: ``ui/panels/texturing_panel.py``.
_TEXTURE_MAP_ATTR = {
    "base_color": "map_base_color",
    "normal": "map_normal",
    "roughness": "map_roughness",
    "metallic": "map_metallic",
}


# ---------------------------------------------------------------------------
# Helpers shared across operators.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tag_view3d_redraw(context) -> None:
    """Tag every ``VIEW_3D`` area in the current screen for redraw.

    Status callbacks update PropertyGroup fields on the main thread but
    Blender does not redraw automatically when a property changes
    out-of-band. Explicitly tagging the area keeps the < 2-second
    refresh budget required by Requirements 5.8 and 8.4 well within
    reach -- the dispatcher ticks every 100 ms.
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
        # Defensive: stale area handles during a hot reload should not
        # crash the status callback path.
        logger.debug("job_ops: tag_redraw failed", exc_info=True)


def _make_main_thread_callback(
    setter: Callable[[str, dict], None],
) -> Callable[[str, dict], None]:
    """Wrap a property-update closure so every event runs on the main thread.

    The :class:`JobExecutor` invokes ``on_status`` from a worker
    thread. Touching ``bpy`` from a worker is unsafe; instead the
    returned closure pushes the real update onto :data:`_callbacks`,
    which the :class:`AITK_OT_job_dispatcher` modal timer drains on the
    Blender main thread (Requirements 13.7, 15.3).

    Worker-thread side: enqueue. Main-thread side: invoke ``setter``
    with the original ``(job_id, payload)`` arguments.

    When :data:`_callbacks` has not been bound yet (addon mid-register
    or mid-reload) the event is logged and dropped rather than
    forwarded to the now-detached panel.
    """

    def _on_status(job_id: str, payload: dict) -> None:
        if _callbacks is None:
            logger.warning(
                "job_ops: callback queue not bound; status event "
                "dropped: %s",
                payload,
            )
            return
        _callbacks.put(setter, job_id, payload)

    return _on_status


def _record_handle(handle: JobHandle) -> None:
    """Store ``handle`` in the live-handle registry keyed by job id."""
    _handles[handle.job_id] = handle


def _drop_handle(job_id: str) -> None:
    """Remove ``job_id`` from the live-handle registry. Idempotent."""
    _handles.pop(job_id, None)


def _executor_failure_reason(job_id: str) -> str:
    """Return ``failure_reason`` recorded by the executor for ``job_id``.

    The :class:`JobExecutor` surfaces ``failure_reason`` only on the
    underlying :class:`Generation_Job` instance. There is no public
    accessor (status callbacks carry only ``status`` and
    ``elapsed_ms``), so we read the protected ``_jobs`` table
    directly. Returns the empty string when the executor has no record
    of the job, when the job is non-terminal, or when the failure
    reason is unset.
    """
    if _executor is None:
        return ""
    job = _executor._jobs.get(job_id)  # noqa: SLF001 - service-layer hook
    if job is None:
        return ""
    return job.failure_reason or ""


def _check_dependencies(operator) -> bool:
    """Report a transient "not ready" error and return ``False`` when
    any of the four dependency slots is unbound.

    Used by every submit operator's :meth:`execute` so the operator
    body can assume each slot is non-``None``. Cancel operators have a
    lighter version that only checks the executor.
    """
    if _executor is None or _callbacks is None or _asset_importer is None:
        operator.report({"ERROR"}, "AI Toolkit is not yet ready")
        return False
    return True


# ===========================================================================
# 16.2 Text-to-3D submit and cancel operators.
# ===========================================================================


class AITK_OT_submit_text_to_3d(bpy.types.Operator):
    """Submit a Text-to-3D Generation_Job (Req 4.2, 4.4, 4.5, 4.9-4.11).

    Reads the prompt from ``scene.ai_toolkit_text_to_3d``, validates
    it (1-1000 chars, non-whitespace -- Req 4.5), submits a
    :class:`TextTo3DRequest` through :class:`JobExecutor`, and
    registers an ``on_status`` closure that:

    * mirrors the executor's status string into ``props.status`` so
      the panel re-renders the active label (Req 4.3);
    * on ``succeeded`` dispatches the returned file path to
      :meth:`AssetImporter.import_model` (Req 4.4); a failed import
      promotes the job into a UI-side ``failed`` state with
      ``failure_reason='the import failed'`` and leaves the scene
      unchanged (Req 4.11);
    * on ``failed`` reads ``failure_reason`` from the executor's job
      record so the panel surfaces the cause (Req 4.10);
    * on ``cancelled`` skips the importer entirely (Req 4.8).

    Synchronous submit failures (queue full, no provider) are reported
    as "the request could not be submitted" (Req 4.9) and the operator
    returns ``{'CANCELLED'}`` without registering a JobHandle.
    """

    bl_idname = "aitk.submit_text_to_3d"
    bl_label = "Generate 3D Model"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        if not _check_dependencies(self):
            return {"CANCELLED"}

        props = getattr(context.scene, "ai_toolkit_text_to_3d", None)
        if props is None:
            self.report({"ERROR"}, "Text-to-3D is not available")
            return {"CANCELLED"}

        prompt = props.prompt or ""
        if not validate_text(prompt, 1, 1000):
            # Req 4.5 verbatim wording.
            self.report({"ERROR"}, "a prompt is required")
            return {"CANCELLED"}

        request = TextTo3DRequest(prompt=prompt)

        def update_props(job_id: str, payload: dict) -> None:
            """Main-thread status callback for one Text-to-3D job."""
            try:
                status = payload.get("status", "")
                if not status:
                    return
                # Req 4.3: status mirrors the executor's label.
                props.status = status

                if status == "succeeded":
                    response = _executor.get_response(job_id) if _executor else None
                    if response is not None and response.output_file_paths:
                        path = response.output_file_paths[0]
                        try:
                            _asset_importer.import_model(path)
                        except ImportError_ as exc:
                            # Req 4.11: scene unchanged (the importer
                            # already rolled back its data-block diff)
                            # and the panel surfaces the cause.
                            props.status = "failed"
                            props.failure_reason = "the import failed"
                            logger.error(
                                "Text-to-3D import failed for %s: %s",
                                path,
                                exc,
                            )
                    _drop_handle(job_id)
                elif status == "failed":
                    # Req 4.10: surface the provider's reason inline.
                    reason = _executor_failure_reason(job_id)
                    props.failure_reason = reason or "generation failed"
                    _drop_handle(job_id)
                elif status == "cancelled":
                    # Req 4.8: no import, panel shows the state.
                    props.failure_reason = "cancelled"
                    _drop_handle(job_id)
                # ``queued`` / ``running`` carry no extra UI work.

                _tag_view3d_redraw(context)
            except Exception:
                logger.exception("Text-to-3D status callback failed")

        callback = _make_main_thread_callback(update_props)

        try:
            handle = _executor.submit(
                "text_to_3d", request, on_status=callback
            )
        except JobQueueFullError:
            self.report({"ERROR"}, "the request could not be submitted")
            return {"CANCELLED"}
        except Exception:
            # Req 4.9: any submission failure surfaces the same message
            # and does NOT track the job.
            logger.exception("Text-to-3D submit failed")
            self.report({"ERROR"}, "the request could not be submitted")
            return {"CANCELLED"}

        # Track the handle so the cancel operator can find it and
        # mirror the initial state onto the PropertyGroup so the panel
        # renders ``queued`` immediately rather than waiting for the
        # first dispatcher tick.
        props.current_job_id = handle.job_id
        props.status = "queued"
        props.failure_reason = ""
        _record_handle(handle)
        return {"FINISHED"}


class AITK_OT_cancel_text_to_3d(bpy.types.Operator):
    """Cancel the active Text-to-3D job (Req 4.7).

    Looks up the live :class:`JobHandle` by the job id stored in
    ``props.current_job_id`` and invokes :meth:`JobHandle.cancel`. The
    executor sets the ``cancel_event`` and the worker transitions the
    job to ``cancelled`` on its next polling tick; the status callback
    above clears ``current_job_id`` and the panel re-renders.
    """

    bl_idname = "aitk.cancel_text_to_3d"
    bl_label = "Cancel"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "ai_toolkit_text_to_3d", None)
        if props is None:
            self.report({"WARNING"}, "no active Text-to-3D job to cancel")
            return {"CANCELLED"}

        job_id = props.current_job_id
        handle = _handles.get(job_id) if job_id else None
        if handle is None:
            self.report({"WARNING"}, "no active Text-to-3D job to cancel")
            return {"CANCELLED"}

        handle.cancel()
        return {"FINISHED"}


# ===========================================================================
# 17.2 Image-to-3D submit and cancel operators.
# ===========================================================================


def _is_decodable_image(path: str) -> bool:
    """Return ``True`` if ``path`` looks like a real, readable image.

    Two-strategy decode check (Req 5.11):

    1. Pillow's ``Image.open(...).verify()`` -- the most thorough check
       short of actually loading pixels. Available in many Blender
       distributions but not all.
    2. Standard library ``imghdr.what(path)`` -- detects the
       canonical image magic numbers without a third-party
       dependency. Returns one of ``"png"``, ``"jpeg"``, ``"webp"``,
       etc. when the file is recognised.

    The function returns ``False`` if both strategies fail (or raise),
    matching the "image cannot be read" rejection path.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001 - Pillow not available in some distros
        Image = None  # type: ignore[assignment]

    if Image is not None:
        try:
            with Image.open(path) as img:
                img.verify()
            return True
        except Exception:
            return False

    # Pillow unavailable; fall back to imghdr's magic-byte sniff. The
    # modern names ``webp`` etc. are recognised in Python 3.6+.
    try:
        kind = imghdr.what(path)
    except Exception:
        return False
    if kind in ("png", "jpeg", "webp"):
        return True
    # imghdr is conservative: it returns ``None`` for files it does not
    # recognise, including some valid WebP variants. Cross-check via a
    # tiny header read so we don't reject those.
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return False


class AITK_OT_submit_image_to_3d(bpy.types.Operator):
    """Submit an Image-to-3D Generation_Job (Req 5.2-5.7, 5.10-5.12).

    Validates the source image up-front (Req 5.3, 5.4, 5.11), submits
    an :class:`ImageTo3DRequest`, and registers an ``on_status``
    closure that:

    * on ``succeeded`` calls :meth:`AssetImporter.validate_model` first
      (Req 5.5); when validation returns ``False`` the operator
      promotes the job into a UI-side ``failed`` state with
      ``failure_reason='invalid_result_file'`` and skips import (Req
      5.6); when validation returns ``True`` the file is imported (Req
      5.7);
    * on ``failed`` surfaces the provider's reason inline and skips
      the importer (Req 5.10);
    * on ``cancelled`` skips both validation and import.
    """

    bl_idname = "aitk.submit_image_to_3d"
    bl_label = "Generate 3D from Image"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        if not _check_dependencies(self):
            return {"CANCELLED"}

        props = getattr(context.scene, "ai_toolkit_image_to_3d", None)
        if props is None:
            self.report({"ERROR"}, "Image-to-3D is not available")
            return {"CANCELLED"}

        image_path = (props.image_path or "").strip()

        # 1. File must exist on disk (Req 5.3 verbatim wording).
        if not image_path or not os.path.isfile(image_path):
            self.report({"ERROR"}, "Image file not found")
            return {"CANCELLED"}

        # 2. Extension must match the picker's allow-list (Req 5.1).
        ext = os.path.splitext(image_path)[1].lower()
        if ext not in _IMAGE_TO_3D_EXTENSIONS:
            self.report({"ERROR"}, "Image file not found")
            return {"CANCELLED"}

        # 3. Size cap (Req 5.4 verbatim wording).
        try:
            size = os.path.getsize(image_path)
        except OSError:
            self.report({"ERROR"}, "Image file not found")
            return {"CANCELLED"}
        if size > _IMAGE_TO_3D_MAX_BYTES:
            self.report({"ERROR"}, "Image exceeds 20 MB size limit")
            return {"CANCELLED"}

        # 4. Decodability (Req 5.11 verbatim wording).
        if not _is_decodable_image(image_path):
            self.report({"ERROR"}, "image cannot be read")
            return {"CANCELLED"}

        request = ImageTo3DRequest(image_path=image_path)

        def update_props(job_id: str, payload: dict) -> None:
            """Main-thread status callback for one Image-to-3D job."""
            try:
                status = payload.get("status", "")
                if not status:
                    return
                props.status = status

                if status == "succeeded":
                    response = _executor.get_response(job_id) if _executor else None
                    if response is None or not response.output_file_paths:
                        # Empty response on a "succeeded" job is
                        # surfaced as an invalid result so the user
                        # gets feedback rather than a silent no-op.
                        props.status = "failed"
                        props.failure_reason = "invalid_result_file"
                    else:
                        path = response.output_file_paths[0]
                        # Req 5.5: validate before importing.
                        if not _asset_importer.validate_model(path):
                            # Req 5.6: promote to failed with the
                            # required reason and skip the import.
                            props.status = "failed"
                            props.failure_reason = "invalid_result_file"
                        else:
                            try:
                                # Req 5.7: only valid files reach the
                                # importer.
                                _asset_importer.import_model(path)
                            except ImportError_ as exc:
                                props.status = "failed"
                                props.failure_reason = "the import failed"
                                logger.error(
                                    "Image-to-3D import failed for %s: %s",
                                    path,
                                    exc,
                                )
                    _drop_handle(job_id)
                elif status == "failed":
                    # Req 5.10: provider's reason verbatim, no import.
                    reason = _executor_failure_reason(job_id)
                    props.failure_reason = reason or "generation failed"
                    _drop_handle(job_id)
                elif status == "cancelled":
                    props.failure_reason = "cancelled"
                    _drop_handle(job_id)

                _tag_view3d_redraw(context)
            except Exception:
                logger.exception("Image-to-3D status callback failed")

        callback = _make_main_thread_callback(update_props)

        try:
            handle = _executor.submit(
                "image_to_3d", request, on_status=callback
            )
        except JobQueueFullError:
            # Req 5.12: submission failure does not transition to running.
            self.report({"ERROR"}, "submission failed")
            return {"CANCELLED"}
        except Exception:
            logger.exception("Image-to-3D submit failed")
            self.report({"ERROR"}, "submission failed")
            return {"CANCELLED"}

        props.current_job_id = handle.job_id
        props.status = "queued"
        props.failure_reason = ""
        _record_handle(handle)
        return {"FINISHED"}


class AITK_OT_cancel_image_to_3d(bpy.types.Operator):
    """Cancel the active Image-to-3D job (mirrors Req 4.7 semantics)."""

    bl_idname = "aitk.cancel_image_to_3d"
    bl_label = "Cancel"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "ai_toolkit_image_to_3d", None)
        if props is None:
            self.report({"WARNING"}, "no active Image-to-3D job to cancel")
            return {"CANCELLED"}

        job_id = props.current_job_id
        handle = _handles.get(job_id) if job_id else None
        if handle is None:
            self.report({"WARNING"}, "no active Image-to-3D job to cancel")
            return {"CANCELLED"}

        handle.cancel()
        return {"FINISHED"}


# ===========================================================================
# 18.3 AI Texturing submit and cancel operators.
# ===========================================================================


def _selected_texture_maps(props) -> tuple[str, ...]:
    """Return the user's currently-selected texture-map kinds in canonical order.

    Reads the four BoolProperty flags on
    :class:`AITK_PG_texturing` and emits a tuple in the canonical
    :data:`_TEXTURE_MAP_KIND_ORDER` order so a downstream provider that
    indexes the result positionally always sees the same order
    regardless of the user's click history.
    """
    return tuple(
        kind
        for kind in _TEXTURE_MAP_KIND_ORDER
        if getattr(props, _TEXTURE_MAP_ATTR[kind], False)
    )


def _selected_meshes(context) -> list:
    """Return the list of MESH objects currently selected (Req 6.3)."""
    selected = getattr(context, "selected_objects", None) or ()
    return [obj for obj in selected if getattr(obj, "type", None) == "MESH"]


def _has_uv_layer(obj) -> bool:
    """Return ``True`` iff ``obj`` carries at least one UV layer (Req 6.4)."""
    try:
        data = obj.data
        if data is None:
            return False
        uv_layers = getattr(data, "uv_layers", None)
        if uv_layers is None:
            return False
        return len(uv_layers) > 0
    except Exception:
        # Treat any inspection failure as "no UV layer" so the warning
        # path runs rather than silently submitting.
        return False


def _build_texture_files_mapping(
    response, selected_kinds: tuple[str, ...]
) -> Dict[str, str]:
    """Map each requested map kind to the provider's output path.

    The provider may communicate the kind -> path mapping in two ways:

    1. ``response.metadata['texture_files']`` -- a dict of
       ``{map_kind: path}``. Preferred because it's unambiguous.
    2. ``response.output_file_paths`` -- a positional tuple of paths
       in the same order the kinds were requested. Fallback for
       providers that don't fill in the metadata.

    The returned dict only contains entries the requested kinds asked
    for AND which the provider actually produced; missing entries are
    silently dropped so the importer never sees a half-built request.
    """
    metadata = getattr(response, "metadata", None) or {}
    metadata_files = metadata.get("texture_files")
    if isinstance(metadata_files, dict) and metadata_files:
        return {
            kind: str(metadata_files[kind])
            for kind in selected_kinds
            if kind in metadata_files
        }

    paths = getattr(response, "output_file_paths", ()) or ()
    return {
        kind: paths[index]
        for index, kind in enumerate(selected_kinds)
        if index < len(paths)
    }


class AITK_OT_submit_texture_generation(bpy.types.Operator):
    """Submit an AI Texturing Generation_Job (Req 6.3-6.9, 13.2).

    Pre-flight checks:

    1. Exactly one mesh selected (Req 6.3 verbatim wording).
    2. Non-empty prompt (Req 6.8).
    3. Selected mesh has at least one UV layer (Req 6.4) -- when it
       does not, the operator surfaces the required warning and
       continues with submission. A full modal-confirm gate is
       deferred per the design document.

    The :class:`TextureGenerationRequest` carries the selected mesh's
    **name** as a plain string -- never the
    :class:`bpy.types.Object` reference -- so the request stays safe
    to cross the UI / Service boundary (Req 13.2).

    On ``succeeded`` the operator builds the canonical material name
    ``f"{object_name}_AI_{job_id[:8]}"`` (Req 6.7 / Property 19) and
    dispatches to :meth:`AssetImporter.attach_texture_maps`. On any
    non-success terminal status the operator surfaces an error and
    leaves existing materials untouched (Req 6.9).
    """

    bl_idname = "aitk.submit_texture_generation"
    bl_label = "Generate Texture"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        if not _check_dependencies(self):
            return {"CANCELLED"}

        props = getattr(context.scene, "ai_toolkit_texturing", None)
        if props is None:
            self.report({"ERROR"}, "AI Texturing is not available")
            return {"CANCELLED"}

        # 1. Mesh selection check (Req 6.3 verbatim wording).
        meshes = _selected_meshes(context)
        if len(meshes) != 1:
            self.report({"ERROR"}, "Select exactly one mesh object")
            return {"CANCELLED"}

        target_obj = meshes[0]
        object_name = target_obj.name

        # 2. Prompt validation (Req 6.8).
        prompt = props.prompt or ""
        if not validate_text(prompt, 1, 1000):
            self.report({"ERROR"}, "a prompt is required")
            return {"CANCELLED"}

        # 3. UV warning (Req 6.4). The verbatim wording is required;
        # we surface it as a Blender ``WARNING`` report and continue.
        # The full modal-confirm gate is deferred per the design.
        if not _has_uv_layer(target_obj):
            self.report(
                {"WARNING"},
                "Selected object has no UV map; "
                "texture results may be incorrect",
            )

        # The texture-map list must be non-empty by construction (the
        # toggle helper enforces "at least one selected"); we still
        # guard defensively because a Blender hot reload or an
        # external script could drop every flag.
        selected_kinds = _selected_texture_maps(props)
        if not selected_kinds:
            self.report({"ERROR"}, "Select at least one texture map")
            return {"CANCELLED"}

        request = TextureGenerationRequest(
            prompt=prompt,
            texture_maps=selected_kinds,
            object_name=object_name,
        )

        def update_props(job_id: str, payload: dict) -> None:
            """Main-thread status callback for one AI Texturing job."""
            try:
                status = payload.get("status", "")
                if not status:
                    return
                props.status = status

                if status == "succeeded":
                    response = _executor.get_response(job_id) if _executor else None
                    if response is None:
                        props.status = "failed"
                        props.failure_reason = "texture generation failed"
                    else:
                        # Req 6.7 / Property 19: f"{object_name}_AI_{job_id[:8]}".
                        material_name = (
                            f"{object_name}_AI_{job_id[:8]}"
                        )
                        texture_files = _build_texture_files_mapping(
                            response, selected_kinds
                        )
                        if not texture_files:
                            props.status = "failed"
                            props.failure_reason = "texture generation failed"
                        else:
                            try:
                                _asset_importer.attach_texture_maps(
                                    object_name,
                                    material_name,
                                    texture_files,
                                )
                            except ImportError_ as exc:
                                # Req 6.9: existing materials untouched
                                # (the importer rolled back already).
                                props.status = "failed"
                                props.failure_reason = (
                                    "texture generation failed"
                                )
                                logger.error(
                                    "AI Texturing attach failed for %s: %s",
                                    object_name,
                                    exc,
                                )
                    _drop_handle(job_id)
                elif status == "failed":
                    # Req 6.9: surface failure, leave materials alone.
                    reason = _executor_failure_reason(job_id)
                    props.failure_reason = (
                        reason or "texture generation failed"
                    )
                    _drop_handle(job_id)
                elif status == "cancelled":
                    props.failure_reason = "cancelled"
                    _drop_handle(job_id)

                _tag_view3d_redraw(context)
            except Exception:
                logger.exception("AI Texturing status callback failed")

        callback = _make_main_thread_callback(update_props)

        try:
            handle = _executor.submit(
                "texture_generation", request, on_status=callback
            )
        except JobQueueFullError:
            self.report({"ERROR"}, "the request could not be submitted")
            return {"CANCELLED"}
        except Exception:
            logger.exception("AI Texturing submit failed")
            self.report({"ERROR"}, "the request could not be submitted")
            return {"CANCELLED"}

        props.current_job_id = handle.job_id
        props.status = "queued"
        props.failure_reason = ""
        _record_handle(handle)
        return {"FINISHED"}


class AITK_OT_cancel_texture_generation(bpy.types.Operator):
    """Cancel the active AI Texturing job (mirrors Req 4.7 semantics)."""

    bl_idname = "aitk.cancel_texture_generation"
    bl_label = "Cancel"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "ai_toolkit_texturing", None)
        if props is None:
            self.report({"WARNING"}, "no active AI Texturing job to cancel")
            return {"CANCELLED"}

        job_id = props.current_job_id
        handle = _handles.get(job_id) if job_id else None
        if handle is None:
            self.report({"WARNING"}, "no active AI Texturing job to cancel")
            return {"CANCELLED"}

        handle.cancel()
        return {"FINISHED"}


# ===========================================================================
# 19.2 Render Preview submit and cancel operators.
# ===========================================================================


def _find_view3d_area(context):
    """Return the first ``VIEW_3D`` area in the current screen, or ``None``.

    Used by both the Render Preview submit operator (Req 7.11) and the
    viewport-screenshot helper. Walks ``context.screen.areas`` so the
    operator can be invoked from any space type and still find a
    viewport to capture.
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in getattr(screen, "areas", ()) or ():
        if getattr(area, "type", None) == "VIEW_3D":
            return area
    return None


def _capture_viewport_screenshot() -> Optional[str]:
    """Capture the active 3D Viewport into a temp PNG and return its path.

    Returns ``None`` on any failure. The caller surfaces this as the
    same "an active 3D Viewport is required" error the no-VIEW_3D
    branch uses, since both states leave the request without an input
    image to send.

    The temp directory comes from :func:`tempfile.gettempdir` so the
    file is on the same volume as Blender's other temp artefacts.
    The filename is timestamped + opened/closed atomically via
    :class:`tempfile.NamedTemporaryFile` so concurrent submissions on
    the same Blender instance never collide.
    """
    try:
        # NamedTemporaryFile gives us an OS-unique path; we close the
        # handle immediately so screenshot_area can re-open it for write.
        fd = tempfile.NamedTemporaryFile(
            prefix="aitk_viewport_", suffix=".png", delete=False
        )
        path = fd.name
        fd.close()
    except OSError:
        logger.exception("Render Preview: cannot create temp screenshot path")
        return None

    try:
        screen_ops = getattr(bpy.ops, "screen", None)
        screenshot_area = (
            getattr(screen_ops, "screenshot_area", None)
            if screen_ops is not None
            else None
        )
        if not callable(screenshot_area):
            # Older / headless Blender builds may not register the
            # operator. Fall back to ``screen.screenshot`` if it
            # exists; otherwise abort and return None.
            screenshot = (
                getattr(screen_ops, "screenshot", None)
                if screen_ops is not None
                else None
            )
            if not callable(screenshot):
                logger.warning(
                    "Render Preview: no screenshot operator available"
                )
                return None
            screenshot(filepath=path)
        else:
            screenshot_area(filepath=path)
    except Exception:
        logger.exception(
            "Render Preview: viewport screenshot capture failed"
        )
        # Best-effort cleanup of the empty temp file we created.
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    return path


class AITK_OT_submit_render_preview(bpy.types.Operator):
    """Submit a Render Preview Generation_Job (Req 7.2, 7.3, 7.5, 7.11).

    Steps:

    1. Find a ``VIEW_3D`` area; abort with the Req 7.11 wording when
       none exists.
    2. Capture a screenshot of that area to a temp PNG. The screenshot
       path is stored on ``props.viewport_screenshot_path`` so the
       comparison view (task 19.1) can render it on every status,
       including ``failed``/``cancelled`` per Req 7.5.
    3. Synchronously build a :class:`SceneSnapshot` and run it through
       :class:`SceneAnalyzer` to produce the scene-description dict
       attached to the request (Req 7.3, 13.2).
    4. Build :class:`RenderPreviewRequest` and submit through the
       executor.

    Status callback updates:

    * ``succeeded`` -- store the returned preview-image path on
      ``props.preview_image_path`` so the panel renders the AI image
      next to the original screenshot (Req 7.4).
    * ``failed`` / ``cancelled`` -- populate ``props.failure_reason``
      and keep ``props.viewport_screenshot_path`` so the comparison
      view still shows the original (Req 7.5).
    """

    bl_idname = "aitk.submit_render_preview"
    bl_label = "Generate Render Preview"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        if not _check_dependencies(self):
            return {"CANCELLED"}

        props = getattr(context.scene, "aitk_render_preview", None)
        if props is None:
            self.report({"ERROR"}, "Render Preview is not available")
            return {"CANCELLED"}

        # 1. VIEW_3D area gate (Req 7.11 verbatim wording).
        area = _find_view3d_area(context)
        if area is None:
            self.report({"ERROR"}, "an active 3D Viewport is required")
            return {"CANCELLED"}

        # 2. Capture the screenshot. A None return here is the same
        # error path as the missing-area branch -- without an input
        # image the request can't proceed.
        screenshot_path = _capture_viewport_screenshot()
        if screenshot_path is None:
            self.report({"ERROR"}, "an active 3D Viewport is required")
            return {"CANCELLED"}

        # 3. Scene description (Req 7.3, 13.2).
        try:
            snapshot = collect_scene_snapshot(context.scene)
            description = SceneAnalyzer().describe(snapshot)
            scene_context = asdict(description)
        except Exception:
            logger.exception(
                "Render Preview: scene capture/describe failed"
            )
            scene_context = {}

        request = RenderPreviewRequest(
            viewport_screenshot_path=screenshot_path,
            scene_description=scene_context,
            prompt=props.prompt or "",
            style_preset=props.style_preset or "photoreal",
            suggestion_mode=bool(props.suggestion_mode),
        )

        def update_props(job_id: str, payload: dict) -> None:
            """Main-thread status callback for one Render Preview job."""
            try:
                status = payload.get("status", "")
                if not status:
                    return
                props.status = status

                if status == "succeeded":
                    response = _executor.get_response(job_id) if _executor else None
                    if response is not None and response.output_file_paths:
                        # Req 7.4: store for the side-by-side view.
                        props.preview_image_path = response.output_file_paths[0]
                    _drop_handle(job_id)
                elif status == "failed":
                    # Req 7.5: keep the screenshot, surface the cause.
                    reason = _executor_failure_reason(job_id)
                    props.failure_reason = reason or "render preview failed"
                    _drop_handle(job_id)
                elif status == "cancelled":
                    props.failure_reason = "cancelled"
                    _drop_handle(job_id)

                _tag_view3d_redraw(context)
            except Exception:
                logger.exception("Render Preview status callback failed")

        callback = _make_main_thread_callback(update_props)

        try:
            handle = _executor.submit(
                "render_preview", request, on_status=callback
            )
        except JobQueueFullError:
            self.report({"ERROR"}, "the request could not be submitted")
            return {"CANCELLED"}
        except Exception:
            logger.exception("Render Preview submit failed")
            self.report({"ERROR"}, "the request could not be submitted")
            return {"CANCELLED"}

        props.current_job_id = handle.job_id
        props.status = "queued"
        props.failure_reason = ""
        # Store the screenshot path *now* so the comparison view can
        # render it as soon as the panel re-draws, even before the
        # provider has produced anything. The preview-image path is
        # cleared until the job succeeds.
        props.viewport_screenshot_path = screenshot_path
        props.preview_image_path = ""
        _record_handle(handle)
        return {"FINISHED"}


class AITK_OT_cancel_render_preview(bpy.types.Operator):
    """Cancel the active Render Preview job."""

    bl_idname = "aitk.cancel_render_preview"
    bl_label = "Cancel"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "aitk_render_preview", None)
        if props is None:
            self.report({"WARNING"}, "no active Render Preview job to cancel")
            return {"CANCELLED"}

        job_id = props.current_job_id
        handle = _handles.get(job_id) if job_id else None
        if handle is None:
            self.report({"WARNING"}, "no active Render Preview job to cancel")
            return {"CANCELLED"}

        handle.cancel()
        return {"FINISHED"}


# ===========================================================================
# 19.3 Save Preview operator.
# ===========================================================================


class AITK_OT_save_preview(bpy.types.Operator):
    """Copy the most recent preview image to a user-chosen path (Req 7.7, 7.12).

    Opens the standard Blender file selector via
    :meth:`bpy.types.WindowManager.fileselect_add`. The default
    filename is timestamped so consecutive saves don't overwrite each
    other.

    On confirm the operator copies ``props.preview_image_path`` to the
    chosen path with :func:`shutil.copyfile`. On any failure (missing
    source, IO error) the operator surfaces the cause (Req 7.12) and
    leaves the in-memory preview path unchanged so the comparison
    view still renders.
    """

    bl_idname = "aitk.save_preview"
    bl_label = "Save Preview"
    bl_options = {"INTERNAL", "REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})

    def invoke(self, context, event):
        """Open the file selector with a timestamped default name."""
        default_name = (
            "render_preview_"
            + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".png"
        )
        try:
            home = os.path.expanduser("~")
        except Exception:
            home = ""
        self.filepath = (
            os.path.join(home, default_name) if home else default_name
        )
        wm = context.window_manager
        wm.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = getattr(context.scene, "aitk_render_preview", None)
        if props is None:
            self.report({"ERROR"}, "no preview image available")
            return {"CANCELLED"}

        src = (props.preview_image_path or "").strip()
        if not src or not os.path.isfile(src):
            self.report({"ERROR"}, "no preview image available")
            return {"CANCELLED"}

        target = (self.filepath or "").strip()
        if not target:
            self.report({"ERROR"}, "no destination path selected")
            return {"CANCELLED"}

        # Ensure the .png extension so the file is recognisable; do
        # NOT modify the source to avoid clobbering the original.
        if os.path.splitext(target)[1].lower() != ".png":
            target = target + ".png"

        try:
            shutil.copyfile(src, target)
        except OSError as exc:
            # Req 7.12: keep the preview in the comparison view,
            # surface the cause inline.
            self.report({"ERROR"}, f"could not save preview: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Preview saved to {target}")
        return {"FINISHED"}


# ===========================================================================
# 20.2 AI Assistant Send and Cancel operators.
# ===========================================================================


def _conversation_from_props(props) -> list:
    """Build a list of :class:`ChatMessage` from ``props.messages``.

    Mirrors the reverse of
    :func:`ui.conversation_handlers._restore_messages`. The result is
    fed into :func:`build_chat_request`, which slices it to the last
    20 entries before submitting (Req 8.6).
    """
    return [
        ChatMessage(
            role=item.role,
            content=item.content,
            timestamp_iso8601=item.timestamp_iso8601,
        )
        for item in props.messages
    ]


def _last_user_message_content(props) -> str:
    """Return the most recent user message's content, or ``""``.

    Used by the Retry operator (task 20.3) to repopulate
    ``props.input_text`` with the failed message before re-submitting.
    """
    for item in reversed(list(props.messages)):
        if item.role == "user":
            return item.content
    return ""


class AITK_OT_send_chat_message(bpy.types.Operator):
    """Submit a chat completion job (Req 8.3-8.8, 8.11).

    Steps:

    1. Validate the input has at least one non-whitespace character
       (Req 8.3). Reject as a ``WARNING`` rather than an ``ERROR``
       because empty submissions are typo-level mistakes the user
       corrects in place.
    2. Append a ``user`` message to ``props.messages`` carrying the
       input text and a fresh ISO-8601 timestamp (Req 8.1).
    3. Append an empty ``assistant`` placeholder message that
       streaming tokens will accumulate into (Req 8.8).
    4. Optionally collect a scene description (Req 8.7).
    5. Build a :class:`ChatCompletionRequest` via
       :func:`build_chat_request` (which slices to the last 20
       messages per Req 8.6).
    6. Submit through the executor with a streaming-aware status
       callback.

    Status callback handling:

    * ``token`` events (provider-emitted, ``payload['event'] ==
      'token'``) -- append ``payload['delta']`` to the last assistant
      message's ``content``.
    * ``failed`` -- replace the placeholder assistant turn with a
      system message carrying the failure reason (Req 8.11). The
      panel's draw method renders system turns inline; the Retry
      operator (task 20.3) is the user's escape hatch (Req 8.5).
    * ``cancelled`` -- mirror behaviour by appending a ``cancelled``
      system note and clearing the placeholder.
    * ``succeeded`` -- the assistant placeholder already holds the
      full content; clear ``props.input_text`` so the composer is
      ready for the next message.

    Synchronous submission failures (queue full, executor unavailable,
    no provider) drop a system message into the conversation and the
    panel renders the Retry control next to it (Req 8.4).
    """

    bl_idname = "aitk.send_chat_message"
    bl_label = "Send"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        if not _check_dependencies(self):
            return {"CANCELLED"}

        props = getattr(context.scene, "ai_toolkit_assistant", None)
        if props is None:
            self.report({"ERROR"}, "AI Assistant is not available")
            return {"CANCELLED"}

        input_text = props.input_text or ""
        # Req 8.3: at least one non-whitespace char.
        if not validate_text(input_text, 1, 4000):
            self.report({"WARNING"}, "Message is required")
            return {"CANCELLED"}

        # 2. Append the user turn.
        user_msg = props.messages.add()
        user_msg.role = "user"
        user_msg.content = input_text
        user_msg.timestamp_iso8601 = _now_iso()

        # 3. Append an empty assistant placeholder. Streaming tokens
        # are appended to its content as they arrive; the full
        # content remains intact when the job succeeds.
        assistant_msg = props.messages.add()
        assistant_msg.role = "assistant"
        assistant_msg.content = ""
        assistant_msg.timestamp_iso8601 = _now_iso()

        # Index of the assistant placeholder so the status callback
        # can find it again. Storing the index is safer than holding
        # the PropertyGroup reference itself; Blender's collection
        # entries are stable as long as the collection isn't cleared.
        assistant_index = len(props.messages) - 1

        # 4. Build the conversation snapshot AFTER appending the user
        # turn so it's included in the last-20 slice. The placeholder
        # assistant turn is included too, which is harmless: providers
        # treat trailing empty assistant messages as "ready for your
        # turn" rather than as content.
        conversation = _conversation_from_props(props)

        # 5. Optional scene context (Req 8.7).
        scene_context_dict: Optional[dict] = None
        if bool(props.include_scene_context):
            try:
                snapshot = collect_scene_snapshot(context.scene)
                description = SceneAnalyzer().describe(snapshot)
                scene_context_dict = asdict(description)
            except Exception:
                logger.exception(
                    "AI Assistant: scene context capture failed"
                )
                scene_context_dict = None

        request = build_chat_request(
            conversation, scene_context=scene_context_dict
        )

        def update_props(job_id: str, payload: dict) -> None:
            """Main-thread status callback for one chat completion job.

            Handles streaming token events as well as the canonical
            five status transitions. The payload's ``event`` key is
            the provider-supplied extra: ``"token"`` for streaming
            deltas, ``"request_started"``/``"completed"`` for non-
            streaming progress beats. The ``status`` key holds the
            executor's lifecycle label.
            """
            try:
                event_kind = payload.get("event")
                if event_kind == "token":
                    # Streaming append (Req 8.8). Resolve the
                    # placeholder by index; if the user has cleared
                    # the conversation since the request was
                    # submitted, drop the delta on the floor.
                    if assistant_index >= len(props.messages):
                        return
                    target = props.messages[assistant_index]
                    if target.role != "assistant":
                        return
                    delta = payload.get("delta", "")
                    if delta:
                        target.content = target.content + delta
                    _tag_view3d_redraw(context)
                    return

                status = payload.get("status", "")
                if not status:
                    return
                props.status = status

                if status == "succeeded":
                    # Streaming providers have already populated the
                    # placeholder's content via repeated token events.
                    # For non-streaming providers the response holds
                    # the full assistant message, so we copy it in if
                    # the placeholder is still empty.
                    response = _executor.get_response(job_id) if _executor else None
                    if (
                        response is not None
                        and assistant_index < len(props.messages)
                    ):
                        target = props.messages[assistant_index]
                        if target.role == "assistant" and not target.content:
                            target.content = (
                                getattr(response, "assistant_message", "") or ""
                            )
                    # Composer cleared per the design's "input cleared
                    # on success" rule.
                    props.input_text = ""
                    _drop_handle(job_id)
                elif status == "failed":
                    # Req 8.11: insert a system message with the
                    # failure reason. The placeholder assistant turn
                    # is repurposed in-place rather than appended so
                    # the conversation history doesn't grow with
                    # empty turns; the Retry control rendered next to
                    # the system message handles re-submission (Req 8.5).
                    reason = _executor_failure_reason(job_id) or "chat failed"
                    if assistant_index < len(props.messages):
                        target = props.messages[assistant_index]
                        target.role = "system"
                        target.content = reason
                        target.timestamp_iso8601 = _now_iso()
                    props.failure_reason = reason
                    _drop_handle(job_id)
                elif status == "cancelled":
                    if assistant_index < len(props.messages):
                        target = props.messages[assistant_index]
                        target.role = "system"
                        target.content = "cancelled"
                        target.timestamp_iso8601 = _now_iso()
                    props.failure_reason = "cancelled"
                    _drop_handle(job_id)

                _tag_view3d_redraw(context)
            except Exception:
                logger.exception("AI Assistant status callback failed")

        callback = _make_main_thread_callback(update_props)

        try:
            handle = _executor.submit(
                "chat_completion", request, on_status=callback
            )
        except (JobQueueFullError, RuntimeError) as exc:
            # Req 8.4: synchronous failure -> system message in the
            # conversation. Re-purpose the placeholder so the panel
            # renders the failure inline next to a Retry control.
            assistant_msg.role = "system"
            assistant_msg.content = str(exc) or "submission failed"
            assistant_msg.timestamp_iso8601 = _now_iso()
            props.failure_reason = assistant_msg.content
            return {"CANCELLED"}
        except Exception as exc:
            logger.exception("AI Assistant submit failed")
            assistant_msg.role = "system"
            assistant_msg.content = str(exc) or "submission failed"
            assistant_msg.timestamp_iso8601 = _now_iso()
            props.failure_reason = assistant_msg.content
            return {"CANCELLED"}

        props.current_job_id = handle.job_id
        props.status = "queued"
        props.failure_reason = ""
        # Clear the composer immediately so the user sees the
        # message landed in the conversation history.
        props.input_text = ""
        _record_handle(handle)
        return {"FINISHED"}


class AITK_OT_cancel_chat_message(bpy.types.Operator):
    """Cancel the active chat completion job."""

    bl_idname = "aitk.cancel_chat_message"
    bl_label = "Cancel"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "ai_toolkit_assistant", None)
        if props is None:
            self.report({"WARNING"}, "no active chat message to cancel")
            return {"CANCELLED"}

        job_id = props.current_job_id
        handle = _handles.get(job_id) if job_id else None
        if handle is None:
            self.report({"WARNING"}, "no active chat message to cancel")
            return {"CANCELLED"}

        handle.cancel()
        return {"FINISHED"}


# ===========================================================================
# 20.3 AI Assistant Retry and New Conversation operators.
# ===========================================================================


class AITK_OT_retry_chat_message(bpy.types.Operator):
    """Resubmit the most recent user message after a failure (Req 8.5).

    Walks ``props.messages`` backwards to find the most recent user
    message and:

    1. Removes the trailing system message (the failure notice
       inserted by the Send operator) so the resubmitted user message
       doesn't end up duplicated below it.
    2. Removes the original user message so the Send operator can
       append it again rather than getting confused by the duplicate.
    3. Sets ``props.input_text`` to the recovered content.
    4. Invokes ``aitk.send_chat_message`` which performs all the
       normal submission validation and side effects.

    No-ops cleanly when there is no user message to retry (e.g. the
    user clicked Retry in a fresh conversation).
    """

    bl_idname = "aitk.retry_chat_message"
    bl_label = "Retry"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "ai_toolkit_assistant", None)
        if props is None:
            self.report({"WARNING"}, "nothing to retry")
            return {"CANCELLED"}

        last_user = _last_user_message_content(props)
        if not last_user:
            self.report({"WARNING"}, "nothing to retry")
            return {"CANCELLED"}

        # Walk backwards and remove every entry from the most recent
        # user message onwards. The ``messages`` collection exposes
        # ``remove(index)``; we stop at the first user message we
        # encounter (inclusive) so an earlier successful exchange
        # remains intact.
        for index in range(len(props.messages) - 1, -1, -1):
            entry = props.messages[index]
            role = entry.role
            try:
                props.messages.remove(index)
            except Exception:
                logger.exception(
                    "Retry: failed to remove message at index %d", index
                )
                self.report({"ERROR"}, "could not retry the request")
                return {"CANCELLED"}
            if role == "user":
                break

        # Refill the composer and re-submit. Calling the operator
        # rather than re-implementing send keeps every Req 8.x rule
        # in one place.
        props.input_text = last_user
        props.failure_reason = ""
        try:
            bpy.ops.aitk.send_chat_message()
        except Exception:
            logger.exception("Retry: send_chat_message dispatch failed")
            self.report({"ERROR"}, "could not retry the request")
            return {"CANCELLED"}
        return {"FINISHED"}


class AITK_OT_new_conversation(bpy.types.Operator):
    """Archive and clear the active conversation (Req 8.10).

    Steps:

    1. Snapshot ``props.messages`` to a list of plain dicts so the
       :class:`HistoryManager` doesn't need to know about Blender
       PropertyGroup types.
    2. Hand the snapshot to
       :meth:`HistoryManager.archive_conversation`. On any failure
       log + report ``ERROR`` but **continue** to step 3 so the user
       can always start fresh -- a failed archive is annoying but a
       stuck UI is worse.
    3. Clear the message collection, the composer, the failure
       reason, and reset the status to ``idle``.
    """

    bl_idname = "aitk.new_conversation"
    bl_label = "New Conversation"
    bl_options = {"INTERNAL", "REGISTER"}

    def execute(self, context):
        props = getattr(context.scene, "ai_toolkit_assistant", None)
        if props is None:
            self.report({"ERROR"}, "AI Assistant is not available")
            return {"CANCELLED"}

        messages = [
            {
                "role": item.role,
                "content": item.content,
                "timestamp_iso8601": item.timestamp_iso8601,
            }
            for item in props.messages
        ]

        # Best-effort archive; on failure surface the cause but still
        # clear the conversation so the user can move on.
        archive_failed = False
        if _history_manager is not None and messages:
            try:
                _history_manager.archive_conversation(messages)
            except Exception as exc:
                logger.exception(
                    "New Conversation: archive_conversation failed"
                )
                self.report(
                    {"ERROR"}, f"could not archive conversation: {exc}"
                )
                archive_failed = True
        elif _history_manager is None and messages:
            # No history manager bound -> the conversation is lost.
            # Surface the loss so the user is aware.
            self.report(
                {"WARNING"},
                "history manager unavailable; conversation not archived",
            )

        # Clear the in-memory state regardless of archive success.
        try:
            props.messages.clear()
        except Exception:
            logger.exception("New Conversation: messages.clear failed")
        props.input_text = ""
        props.failure_reason = ""
        props.status = "idle"
        props.current_job_id = ""

        if not archive_failed:
            self.report({"INFO"}, "Started a new conversation")
        return {"FINISHED"}


# ===========================================================================
# Public surface.
# ===========================================================================


__all__ = [
    "AITK_OT_submit_text_to_3d",
    "AITK_OT_cancel_text_to_3d",
    "AITK_OT_submit_image_to_3d",
    "AITK_OT_cancel_image_to_3d",
    "AITK_OT_submit_texture_generation",
    "AITK_OT_cancel_texture_generation",
    "AITK_OT_submit_render_preview",
    "AITK_OT_cancel_render_preview",
    "AITK_OT_save_preview",
    "AITK_OT_send_chat_message",
    "AITK_OT_cancel_chat_message",
    "AITK_OT_retry_chat_message",
    "AITK_OT_new_conversation",
    "bind_dependencies",
]
