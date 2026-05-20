"""AITK_OT_job_dispatcher modal timer drains the CallbackQueue on the main thread.

This module is the **bridge between worker-thread status events and the
Blender main thread**. Worker threads in
:meth:`ai_toolkit.services.jobs.executor.JobExecutor._worker_run` produce
status events; the executor's status callback is the user-supplied closure
registered at :meth:`~ai_toolkit.services.jobs.executor.JobExecutor.submit`
time. That closure typically wants to update a ``bpy.types.PropertyGroup``,
redraw a panel, show a notification, or import a file via the AssetImporter
— all of which must run on the Blender main thread. Worker threads cannot
touch ``bpy`` directly, so they push the closure (and its arguments) onto a
:class:`~ai_toolkit.services.jobs.callbacks.CallbackQueue`, and this modal
operator drains the queue every ``TICK_INTERVAL_S`` seconds on the main
thread (Requirements 13.7, 15.3).

Lifecycle
---------
The dispatcher is a one-shot singleton: at most one modal instance is
running at any time. The intended startup order, performed by the addon's
top-level ``__init__.register()`` (task 24.1), is:

1. ``bpy.utils.register_class(AITK_OT_job_dispatcher)``
2. :meth:`AITK_OT_job_dispatcher.bind_queue` — attach the
   :class:`CallbackQueue` instance built by the service layer.
3. :meth:`AITK_OT_job_dispatcher.start` — launch the modal operator.

``__init__.unregister()`` mirrors this in reverse:

1. :meth:`AITK_OT_job_dispatcher.stop` — request the modal loop to exit.
2. ``bpy.utils.unregister_class(AITK_OT_job_dispatcher)``

Tick interval
-------------
``TICK_INTERVAL_S = 0.1`` (100 ms). Requirement 15.2 mandates a tick
interval in the closed interval [50 ms, 250 ms]; 100 ms is the midpoint
that matches the existing ``AIToolkitGenerateImage`` timer cadence in
``operators.py`` and keeps UI feedback latency well under the 2-second
status-update bound stated in Requirements 5.8 and 8.4.

Exception isolation
-------------------
:class:`CallbackQueue` already catches per-callback exceptions internally
(Requirements 13.8, 15.9). The dispatcher additionally wraps the call to
:meth:`~ai_toolkit.services.jobs.callbacks.CallbackQueue.drain` in a
top-level try/except so that even an unexpected failure in the queue
machinery (for example, ``Queue.get_nowait`` raising in a future Python
runtime) cannot crash the modal handler — instead the failure is logged
and the next tick simply tries again.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Optional

import bpy

from ..services.jobs.callbacks import CallbackQueue

__all__ = ["AITK_OT_job_dispatcher", "TICK_INTERVAL_S"]


# Addon-wide logger configured in ``services/__init__.py``.
logger = logging.getLogger("ai_toolkit")

# 100 ms — the midpoint of Requirement 15.2's [50 ms, 250 ms] bound.
TICK_INTERVAL_S: float = 0.1


class AITK_OT_job_dispatcher(bpy.types.Operator):
    """Modal timer that drains the :class:`CallbackQueue` every tick.

    A single instance runs at a time. :meth:`start` invokes the operator
    via ``bpy.ops.aitk.job_dispatcher('INVOKE_DEFAULT')``; the running
    modal calls :meth:`CallbackQueue.drain` on every ``TIMER`` event and
    exits when :meth:`stop` is called.
    """

    bl_idname = "aitk.job_dispatcher"
    bl_label = "AI Toolkit Job Dispatcher"
    bl_options = {"INTERNAL"}

    # Class-level storage for the singleton instance bookkeeping.
    # ``bpy.types.Operator`` instances are short-lived; the class object
    # itself survives across ``invoke`` calls, so the bind/start/stop
    # control plane lives here.
    _instance: ClassVar[Optional["AITK_OT_job_dispatcher"]] = None
    _queue: ClassVar[Optional[CallbackQueue]] = None
    _stop_requested: ClassVar[bool] = False

    # Per-instance bpy timer handle returned by ``wm.event_timer_add``.
    _timer = None

    # ------------------------------------------------------------------
    # Class-level control plane (called by addon register/unregister).
    # ------------------------------------------------------------------
    @classmethod
    def bind_queue(cls, queue: CallbackQueue) -> None:
        """Bind the :class:`CallbackQueue` the dispatcher drains.

        Called by the addon's top-level ``__init__.register()`` (task
        24.1) **before** :meth:`start`. Idempotent: a subsequent call
        replaces the queue reference, which is the desired behaviour
        when Blender reloads the addon.
        """
        cls._queue = queue

    @classmethod
    def start(cls) -> None:
        """Start the dispatcher modal operator.

        Idempotent: when the dispatcher is already running (``_instance``
        is non-None), this is a no-op. Otherwise it clears the stop flag
        and invokes the operator via ``bpy.ops.aitk.job_dispatcher``. If
        the invocation raises (for example, because Blender is shutting
        down or the operator class is not yet registered), the error is
        logged and never propagated, so addon registration cannot be
        derailed by a transient bpy state.

        Must be called *after* :meth:`bind_queue`.
        """
        if cls._instance is not None:
            return
        cls._stop_requested = False
        try:
            bpy.ops.aitk.job_dispatcher("INVOKE_DEFAULT")
        except Exception:
            # Blender raises a wide variety of exception types from
            # operator dispatch (RuntimeError, AttributeError when the
            # op isn't registered, Blender-specific errors during
            # shutdown). Logging is enough — the dispatcher will simply
            # remain inactive until the next ``start`` call.
            logger.warning(
                "Job dispatcher: could not invoke AITK_OT_job_dispatcher; "
                "dispatcher will remain inactive",
                exc_info=True,
            )

    @classmethod
    def stop(cls) -> None:
        """Request the dispatcher to stop on the next tick.

        Sets a class-level flag that :meth:`modal` observes on its next
        ``TIMER`` event, returning ``{'CANCELLED'}`` and tearing down
        the timer. Idempotent. Called by the addon's top-level
        ``__init__.unregister()`` (task 24.1).
        """
        logger.info("Job dispatcher stopping…")
        cls._stop_requested = True

    # ------------------------------------------------------------------
    # bpy.types.Operator overrides.
    # ------------------------------------------------------------------
    def invoke(self, context, event):
        """Start the modal timer and register the operator as the
        running dispatcher instance.
        """
        cls = type(self)
        if cls._queue is None:
            logger.warning(
                "Job dispatcher: CallbackQueue not bound; refusing to start"
            )
            return {"CANCELLED"}

        cls._instance = self

        wm = context.window_manager
        self._timer = wm.event_timer_add(
            TICK_INTERVAL_S, window=context.window
        )
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        """On every ``TIMER`` tick, drain the :class:`CallbackQueue`.

        Exits when :meth:`stop` has been called by clearing the timer
        and the singleton instance pointer and returning
        ``{'CANCELLED'}``. Any non-timer event is passed through so
        other modal handlers continue to receive input (Requirement
        15.2's responsiveness target).
        """
        cls = type(self)

        if cls._stop_requested:
            self.cancel(context)
            cls._instance = None
            return {"CANCELLED"}

        if event.type == "TIMER":
            queue = cls._queue
            if queue is not None:
                try:
                    queue.drain()
                except Exception:
                    # CallbackQueue.drain already swallows per-callback
                    # exceptions; reaching this branch implies an
                    # unexpected failure inside the queue machinery
                    # itself. Log and continue — the next tick will try
                    # again.
                    logger.exception(
                        "Job dispatcher: drain raised; continuing"
                    )

        # Pass-through so other handlers (modal operators, the launcher
        # menu, etc.) keep receiving events.
        return {"PASS_THROUGH"}

    def cancel(self, context):
        """Tear down the timer cleanly.

        Called by Blender when the modal handler is cancelled (either
        via :meth:`stop` or because Blender is reclaiming the modal
        slot). Best-effort: a final :meth:`CallbackQueue.drain` runs so
        that any pending callbacks reach the main thread before the
        dispatcher exits, then the timer is removed.
        """
        cls = type(self)

        # Best-effort final drain so terminal status events (e.g. an
        # asset import callback queued just before unregister) are not
        # silently dropped.
        queue = cls._queue
        if queue is not None:
            try:
                queue.drain()
            except Exception:
                logger.exception(
                    "Job dispatcher: final drain raised during cancel"
                )

        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                # Blender may have already torn the timer down (e.g.
                # during shutdown). Log at debug level only.
                logger.debug(
                    "Job dispatcher: event_timer_remove raised; "
                    "timer was likely already removed",
                    exc_info=True,
                )
            self._timer = None
