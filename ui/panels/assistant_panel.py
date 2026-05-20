"""AI Assistant PropertyGroup collection, panel, and 'Show panel' operator.

This module is part of the **UI Layer** and is therefore allowed to import
``bpy`` freely. It implements the persistent state and panel chrome for the
AI Assistant Generation_Module:

* :class:`AITK_PG_chat_message` mirrors
  :class:`ai_toolkit.services.models.requests.ChatMessage` so a conversation
  can be stored in a ``bpy.types.PropertyGroup`` collection on the active
  scene (``scene.ai_toolkit_assistant.messages``). Storing the conversation
  on the scene is what makes it persist with the .blend file (Req 8.12,
  8.13); the actual save/load handlers that translate the collection to a
  serialised string and back live in :mod:`ui.conversation_handlers`
  (task 20.4).
* :class:`AITK_PG_assistant` holds the per-scene module state: the
  conversation, the user input buffer (capped at
  :data:`CHAT_INPUT_MAXLEN` = 4000 characters per Req 8.2), the
  "Include scene context" toggle (Req 8.7, wired to the Scene_Analyzer in
  task 22.2), and observable job status fields used by the panel chrome.
* :class:`AITK_OT_show_assistant_panel` is the dispatcher target invoked by
  the Launcher Menu's "AI Assistant" entry (Req 2.3); it focuses the
  sidebar tab so the panel is visible.
* :class:`AITK_PT_assistant_panel` draws the conversation view, the
  composer, and the status / cancel row.

The Send / Cancel / New Conversation / Retry operators referenced by the
panel's draw method (``aitk.send_chat_message``, ``aitk.cancel_chat_message``,
``aitk.new_conversation``) are implemented in tasks 20.2 and 20.3. The panel
references their ``bl_idname``\\s by string only, so this module does not
need to import them; if they are not yet registered when the panel draws,
Blender will render the buttons as disabled placeholders, which is the
expected behaviour during the staged registration in task 24.1.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

import bpy

from ..scene_capture import collect_scene_snapshot
from ...services.scene.analyzer import (
    EMPTY_SCENE_MESSAGE,
    SceneAnalyzer,
    group_suggestions,
)

if TYPE_CHECKING:
    # Imported only for type-checking so this module stays free of an
    # import-time dependency on ``services.jobs.executor`` -- the
    # executor is a runtime-bound singleton, not a class we instantiate
    # here. The runtime reference is held in :data:`_executor` and
    # populated by :func:`bind_executor` (called from
    # ``__init__.register()`` in task 24.1).
    from ...services.jobs.executor import JobExecutor

logger = logging.getLogger("ai_toolkit")


__all__ = [
    "AITK_PG_chat_message",
    "AITK_PG_assistant",
    "AITK_OT_show_assistant_panel",
    "AITK_OT_request_scene_suggestions",
    "AITK_PT_assistant_panel",
    "CHAT_INPUT_MAXLEN",
    "bind_executor",
]


# Module-level reference to the addon's :class:`JobExecutor` singleton.
# Populated by :func:`bind_executor` at addon-register time (task 24.1)
# and cleared (set back to ``None``) at unregister time so a stale
# binding cannot survive an addon reload. The
# :class:`AITK_OT_request_scene_suggestions` operator gates its
# ``execute`` on this being set, so the binding lifecycle is the only
# coupling between this module and the JobExecutor's construction --
# importing :mod:`ai_toolkit.services.jobs.executor` here directly would
# pull half the service layer into the panel's import graph for no good
# reason and make the panel impossible to import in the bpy-stubbed
# smoke tests.
_executor: Optional["JobExecutor"] = None


def bind_executor(executor: Optional["JobExecutor"]) -> None:
    """Bind the JobExecutor used by :class:`AITK_OT_request_scene_suggestions`.

    Called by ``__init__.register()`` (task 24.1) before the panel is
    exposed. Passing ``None`` (e.g. from ``__init__.unregister()``)
    clears the binding so the operator's ``execute`` reports a clean
    "not yet ready" error rather than dereferencing a stale executor
    after teardown.
    """
    global _executor
    _executor = executor


CHAT_INPUT_MAXLEN = 4000
"""Maximum length of the AI Assistant text input (Req 8.2).

Mirrors ``bpy.props.StringProperty(maxlen=...)`` enforcement: Blender
truncates user input at this length at the property level, so a separate
UI-side guard is unnecessary.
"""


class AITK_PG_chat_message(bpy.types.PropertyGroup):
    """One message in the AI Assistant conversation (Req 8.1, 8.12, 8.13).

    Field shape mirrors
    :class:`ai_toolkit.services.models.requests.ChatMessage` (the
    bpy-free dataclass used in ``ChatCompletionRequest``) so the UI-side
    collection can be converted to a tuple of dataclass instances by
    :func:`ai_toolkit.services.chat.build_chat_request` without any
    field renaming.

    The collection is stored on
    ``scene.ai_toolkit_assistant.messages``; because Blender persists
    every PropertyGroup attached to the scene with the .blend file,
    storing the conversation here is what satisfies Req 8.12 and 8.13.
    """

    role: bpy.props.StringProperty(name="Role", default="user")
    content: bpy.props.StringProperty(name="Content", default="")
    timestamp_iso8601: bpy.props.StringProperty(name="Timestamp", default="")


class AITK_PG_assistant(bpy.types.PropertyGroup):
    """AI Assistant module state, attached to the active scene.

    All fields are stored on the scene PropertyGroup so they persist with
    the .blend file and so individual scenes can hold independent
    conversations.
    """

    messages: bpy.props.CollectionProperty(type=AITK_PG_chat_message)
    """The conversation, in chronological order (Req 8.1)."""

    input_text: bpy.props.StringProperty(
        name="Message",
        default="",
        maxlen=CHAT_INPUT_MAXLEN,
    )
    """The user's in-progress message (Req 8.2). Cleared by Send."""

    include_scene_context: bpy.props.BoolProperty(
        name="Include scene context",
        default=False,
    )
    """When True, Send attaches a Scene_Analyzer description (Req 8.7).

    The toggle's wiring to :mod:`services.scene.analyzer` is implemented
    in task 22.2; the field is declared here so the toggle persists with
    the .blend file like the rest of the module state.
    """

    status: bpy.props.StringProperty(name="Status", default="idle")
    """Current job status, one of ``idle`` / ``queued`` / ``running`` /
    ``succeeded`` / ``failed`` / ``cancelled``. Drives Send/Cancel button
    enable state in :meth:`AITK_PT_assistant_panel.draw`.
    """

    failure_reason: bpy.props.StringProperty(name="Failure", default="")
    """Last terminal-failure reason. Rendered inline by the panel; cleared
    on the next successful submission (task 20.2).
    """

    current_job_id: bpy.props.StringProperty(name="Job", default="")
    """The active ``Generation_Job.job_id`` while a chat completion is in
    flight; empty string otherwise. Used by the Cancel and Retry
    operators (task 20.3) to address the right job.
    """

    suggestions_status: bpy.props.StringProperty(
        name="Suggestions Status",
        default="idle",
    )
    """Scene-suggestions job status, one of ``idle`` / ``queued`` /
    ``running`` / ``succeeded`` / ``failed`` / ``cancelled`` (task 22.2,
    Req 9.4 / 9.5 / 9.9). Independent of the chat-completion ``status``
    field above so the panel can render scene analysis and chat
    progress side by side.
    """

    suggestions_text: bpy.props.StringProperty(
        name="Suggestions Text",
        default="",
    )
    """Flattened display text for the latest succeeded scene-analysis
    job: either the literal :data:`EMPTY_SCENE_MESSAGE` (Req 9.5) or the
    grouped 5-bucket suggestions (Req 9.6) rendered as plain text. The
    panel splits this on newlines for display.
    """

    suggestions_failure: bpy.props.StringProperty(
        name="Suggestions Failure",
        default="",
    )
    """Failure reason for the most recent terminal scene-analysis job
    (Req 9.9). Rendered as an inline error label by the panel.
    """


class AITK_OT_show_assistant_panel(bpy.types.Operator):
    """Bring the AI Assistant panel into focus.

    The Launcher Menu's "AI Assistant" entry (Req 2.3) invokes this
    operator; the actual focus logic (locating the 3D Viewport sidebar
    and switching to the "AI Toolkit" tab) is wired in task 24.1 along
    with the rest of the addon's register/unregister chain. For now the
    operator is a no-op placeholder so the menu has a stable
    ``bl_idname`` to dispatch to.
    """

    bl_idname = "aitk.show_assistant_panel"
    bl_label = "Show AI Assistant"
    bl_options = {"INTERNAL"}


class AITK_OT_request_scene_suggestions(bpy.types.Operator):
    """Capture the scene snapshot and ask for AI suggestions (task 22.2).

    Implements the AI Assistant's Scene Analysis sub-section: when the
    user activates the "Get Suggestions" control on the panel, this
    operator runs the UI-side scene capture
    (:func:`ai_toolkit.ui.scene_capture.collect_scene_snapshot`), hands
    the snapshot to the bpy-free
    :class:`~ai_toolkit.services.scene.analyzer.SceneAnalyzer`, and
    routes the analyzer's response back into the panel's
    ``suggestions_*`` PropertyGroup fields.

    The analyzer's contract has two branches (Req 9.4, 9.5):

    * **Empty scene.** The analyzer returns the literal
      :data:`~ai_toolkit.services.scene.analyzer.EMPTY_SCENE_MESSAGE`
      string and does *not* submit a Generation_Job. The operator
      stores the message in ``suggestions_text``, sets
      ``suggestions_status = "succeeded"`` so the panel renders the
      static message inline, and returns ``{'FINISHED'}``.
    * **Non-empty scene.** The analyzer submits a ``scene_analysis``
      Generation_Job through the bound :class:`JobExecutor` and returns
      the resulting JobHandle. The operator registers a status
      callback that pulls the response off the executor when the job
      reaches ``succeeded``, runs it through
      :func:`~ai_toolkit.services.scene.analyzer.group_suggestions`
      to bucket suggestions into the canonical five categories
      (Req 9.6), and writes the flattened text to ``suggestions_text``.

    Failure branches (Req 9.9):

    * **``failed`` terminal status.** The callback writes
      ``"Scene analysis failed: <reason>"`` to ``suggestions_failure``;
      the panel renders this as an inline error label and the active
      scene is left untouched.
    * **> 60 s elapsed.** Treated as a timeout; ``suggestions_failure``
      receives ``"Scene analysis failed: timeout (>60s)"``. The
      JobExecutor's own watchdog (600 s, Req 15.5) is a coarser
      backstop; the AI Assistant's product-level limit is 60 s and is
      enforced here at the callback site.
    """

    bl_idname = "aitk.request_scene_suggestions"
    bl_label = "Get Suggestions"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        if _executor is None:
            # The JobExecutor has not yet been bound (e.g. the addon is
            # mid-register or has been unregistered). Surface a clean
            # error rather than dereferencing ``None``; the panel's
            # button is also gated on the executor being bound, so this
            # branch is mainly defensive.
            self.report({"ERROR"}, "AI Toolkit is not yet ready")
            return {"CANCELLED"}

        props = getattr(context.scene, "ai_toolkit_assistant", None)
        if props is None:
            # The PropertyGroup pointer hasn't been wired up yet (staged
            # registration in task 24.1). Same defensive bail as above.
            self.report({"ERROR"}, "AI Assistant not initialised")
            return {"CANCELLED"}

        # Reset the prior run's display fields BEFORE the snapshot so a
        # repeated click reliably clears the previous error / text even
        # if the snapshot or submit raises below.
        props.suggestions_failure = ""
        props.suggestions_text = ""
        props.suggestions_status = "queued"

        snapshot = collect_scene_snapshot(context.scene)
        analyzer = SceneAnalyzer()

        # Capture the executor reference at submit time so the closure
        # below survives a subsequent ``bind_executor(None)`` -- the
        # in-flight job's response still needs to be retrievable.
        executor = _executor

        def on_status(job_id, payload):
            """Worker-thread callback. Updates the panel fields.

            The JobExecutor's contract is to deliver this callback on
            worker threads; the addon-wide CallbackQueue + Job
            Dispatcher (task 14.1) ensures the actual invocation lands
            on the main thread so the ``props.*`` writes are safe.
            Any exception inside the callback is logged and swallowed
            so a buggy callback never crashes the worker (Req 13.8 /
            15.9 already guarantee this at the executor layer; the
            try/except here is belt-and-braces for the panel's own
            attribute writes).
            """
            try:
                status = payload.get("status", "")
                props.suggestions_status = status
                if status == "succeeded":
                    response = executor.get_response(job_id)
                    if response is not None and hasattr(response, "suggestions"):
                        category_hints = getattr(response, "category_hints", {})
                        if category_hints:
                            grouped = group_suggestions(category_hints)
                        else:
                            grouped = group_suggestions(list(response.suggestions))
                        # Flatten into the StringProperty's text form:
                        # one section per non-empty bucket, header
                        # capitalised, items prefixed with "- ".
                        parts = []
                        for cat, items in grouped.items():
                            if items:
                                body = "\n".join(f"  - {it}" for it in items)
                                parts.append(f"{cat.title()}:\n{body}")
                        props.suggestions_text = (
                            "\n\n".join(parts)
                            if parts
                            else "(no suggestions returned)"
                        )
                    else:
                        props.suggestions_text = "(no suggestions returned)"
                elif status == "failed":
                    elapsed_ms = payload.get("elapsed_ms", 0)
                    if elapsed_ms > 60000:
                        # Req 9.9: > 60 s timeout takes priority over
                        # whatever specific reason the executor recorded.
                        reason = "timeout (>60s)"
                    else:
                        reason = (
                            payload.get("failure_reason", "")
                            or "see addon log"
                        )
                    props.suggestions_failure = (
                        f"Scene analysis failed: {reason}"
                    )
                # Refresh the panel so the status / text fields the
                # callback just wrote actually appear without waiting
                # for an unrelated redraw.
                area = getattr(context, "area", None)
                if area is not None:
                    try:
                        area.tag_redraw()
                    except Exception:
                        # tag_redraw failure is non-fatal; the panel
                        # will still update on the next natural redraw.
                        logger.debug(
                            "scene-suggestions tag_redraw failed",
                            exc_info=True,
                        )
            except Exception:
                logger.exception(
                    "scene-suggestions on_status callback raised"
                )

        result = analyzer.request_suggestions(
            snapshot, executor, on_status=on_status
        )

        if isinstance(result, str):
            # Empty-scene branch (Req 9.5). The analyzer returns the
            # literal EMPTY_SCENE_MESSAGE and does NOT submit a job.
            # The panel renders this verbatim through the same
            # ``suggestions_text`` field used for grouped suggestions.
            props.suggestions_status = "succeeded"
            props.suggestions_text = result
            return {"FINISHED"}

        # Non-empty scene: ``result`` is a JobHandle; the on_status
        # callback registered above will populate the fields when the
        # job reaches a terminal state. Status was already set to
        # ``queued`` ahead of the submission so the panel reflects the
        # in-flight state immediately.
        return {"FINISHED"}


class AITK_PT_assistant_panel(bpy.types.Panel):
    """Sidebar panel for the AI Assistant Generation_Module.

    Renders the conversation view, the composer (input + Include scene
    context toggle + Send), the New Conversation control, and a status
    line with a Cancel button. Lives in the 3D Viewport sidebar under the
    "AI Toolkit" tab so it shares space with the other module panels and
    with the Launcher overlay's fallback entry point (Req 1.11).
    """

    bl_idname = "AITK_PT_assistant_panel"
    bl_label = "AI Assistant"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Toolkit"

    def draw(self, context):
        """Render the panel chrome.

        The panel reads from ``context.scene.ai_toolkit_assistant`` if it
        exists; when the addon has been partially registered (e.g. the
        scene PropertyGroup pointer hasn't been wired up yet) it falls
        back to a single error label rather than raising, keeping the
        panel safe to draw at any point during the staged register
        sequence in task 24.1.
        """
        layout = self.layout
        props = getattr(context.scene, "ai_toolkit_assistant", None)
        if props is None:
            layout.label(text="AI Toolkit not fully registered", icon="ERROR")
            return

        # Conversation view (Req 8.1: scrollable, alternating user /
        # assistant in chronological order). Blender doesn't expose a
        # native scroll widget for boxes, but a column of labels in the
        # sidebar grows downward and is scrollable as part of the
        # sidebar's region scroll, which satisfies the requirement
        # without a custom modal.
        box = layout.box()
        box.label(text="Conversation:", icon="OUTLINER_OB_LIGHT")

        if len(props.messages) == 0:
            box.label(text="(no messages yet)")
        else:
            for msg in props.messages:
                sub = box.box()
                # Role-distinguishing icons make alternating user /
                # assistant turns visually obvious without colour.
                icon = "USER" if msg.role == "user" else "OUTLINER_OB_ARMATURE"
                if msg.role == "system":
                    icon = "INFO"
                sub.label(text=f"{msg.role}:", icon=icon)
                # Multi-line content -- split on newlines and render each
                # line as a separate label so the box auto-resizes
                # vertically. ``splitlines() or [content]`` ensures
                # single-line content (no '\n') still renders one label.
                for line in msg.content.splitlines() or [msg.content]:
                    sub.label(text=line)

        # Composer (Req 8.2: text input + Send; Req 8.7: Include scene
        # context toggle; Req 8.9: New Conversation control).
        layout.prop(props, "input_text", text="Message")
        layout.prop(props, "include_scene_context", text="Include scene context")

        status = props.status

        # Send is enabled only when no chat job is in flight. The status
        # set here mirrors the JobStatus enum's terminal members plus
        # ``idle`` (the default before any submission).
        row = layout.row()
        row.enabled = status in ("idle", "succeeded", "failed", "cancelled")
        row.operator("aitk.send_chat_message", text="Send", icon="EXPORT")

        # New Conversation is meaningful only when there is a
        # conversation to archive (Req 8.10).
        new_row = layout.row()
        new_row.enabled = len(props.messages) > 0
        new_row.operator(
            "aitk.new_conversation", text="New Conversation", icon="FILE_NEW"
        )

        layout.label(text=f"Status: {status}", icon="INFO")
        if props.failure_reason:
            layout.label(text=f"Error: {props.failure_reason}", icon="ERROR")

        # Cancel only matters while the job is queued or running; the
        # JobExecutor rejects cancel calls in any other state.
        cancel_row = layout.row()
        cancel_row.enabled = status in ("queued", "running")
        cancel_row.operator(
            "aitk.cancel_chat_message", text="Cancel", icon="CANCEL"
        )

        # ----------------------------------------------------------------
        # Scene Analysis sub-section (task 22.2; Req 9.4 / 9.5 / 9.6 / 9.9)
        # ----------------------------------------------------------------
        # Rendered below the chat composer so it lives alongside the
        # "Include scene context" toggle that exposes the same
        # SceneAnalyzer machinery for chat. The "Get Suggestions"
        # button submits a free-standing scene_analysis job whose
        # results land in the ``suggestions_*`` PropertyGroup fields;
        # the chat path uses the description dict inline as
        # ChatCompletionRequest.scene_context.
        analysis_box = layout.box()
        analysis_box.label(text="Scene Analysis:", icon="SCENE_DATA")

        # The button is gated on the JobExecutor binding so a click
        # before ``register()`` finishes (or after ``unregister()``)
        # cannot fire the operator.
        analysis_row = analysis_box.row()
        analysis_row.enabled = _executor is not None
        analysis_row.operator(
            "aitk.request_scene_suggestions",
            text="Get Suggestions",
            icon="ZOOM_ALL",
        )

        suggestions_status = props.suggestions_status

        if props.suggestions_failure:
            # Req 9.9: render the failure inline; the scene was not
            # modified.
            analysis_box.label(
                text=props.suggestions_failure, icon="ERROR"
            )
        elif (
            suggestions_status == "succeeded" and props.suggestions_text
        ):
            # Either the static EMPTY_SCENE_MESSAGE (Req 9.5) or the
            # grouped-suggestions text (Req 9.6). Each non-empty line
            # becomes its own label so the section headers and items
            # render as a vertical list.
            for line in props.suggestions_text.splitlines() or [
                props.suggestions_text
            ]:
                if line:
                    analysis_box.label(text=line)
        elif suggestions_status in ("queued", "running"):
            analysis_box.label(
                text=f"Analysis in progress... ({suggestions_status})",
                icon="TIME",
            )
        # Idle / unknown statuses: render nothing extra below the
        # button. The user only sees the section's label and trigger.
