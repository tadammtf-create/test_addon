"""Thread-safe queue for delivering callbacks to the Blender main thread.

Worker threads (those running ``Generation_Job``s in the executor pool)
must not invoke ``bpy`` directly. Instead, they push closures onto a
:class:`CallbackQueue` via :meth:`CallbackQueue.put`. The UI-side Job
Dispatcher modal timer (built in a later task) calls
:meth:`CallbackQueue.drain` every ~100 ms, which invokes every pending
callback on the Blender main thread (Requirements 13.7, 15.3).

Per Requirement 13.1, this module is ``bpy``-free: it relies only on the
Python standard library (``queue``, ``logging``, ``typing``).

Per Requirements 13.8 and 15.9, exceptions raised by individual callbacks
are caught and logged to the addon-wide ``ai_toolkit`` logger so that a
misbehaving callback never prevents subsequent callbacks from running.
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Callable

__all__ = ["CallbackQueue"]


# Use the addon-wide logger configured in ``services/__init__.py``.
logger = logging.getLogger("ai_toolkit")


class CallbackQueue:
    """Thread-safe FIFO of pending main-thread callbacks.

    Worker threads enqueue ``(callable, args, kwargs)`` triples via
    :meth:`put`; the Blender main-thread Job Dispatcher consumes them via
    :meth:`drain`.

    Internally backed by :class:`queue.Queue`, which is itself
    thread-safe, so no additional locking is performed by this class.
    """

    def __init__(self) -> None:
        # Unbounded: we never want a status callback dropped on the floor
        # just because the main-thread dispatcher tick was momentarily slow.
        self._queue: queue.Queue[
            tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = queue.Queue()

    def put(
        self,
        callback: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Enqueue ``callback`` to be invoked on the main thread.

        ``args`` and ``kwargs`` are captured at the time of this call and
        will be passed verbatim to ``callback`` when :meth:`drain` runs.

        ``callback`` is positional-only so that any keyword name
        (including ``"callback"``) may appear in ``kwargs`` and be
        forwarded transparently to the wrapped callable.
        """
        self._queue.put((callback, args, kwargs))

    def drain(self) -> int:
        """Invoke every callback currently pending in the queue.

        Returns the number of callbacks invoked.

        Exceptions raised by an individual callback are caught and
        logged so that a single misbehaving callback cannot block any of
        the others, satisfying Requirements 13.8 and 15.9.
        """
        invoked = 0
        while True:
            try:
                callback, args, kwargs = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args, **kwargs)
            except Exception:
                # Required by Requirements 13.8 and 15.9: catch every
                # exception so the rest of the queue still drains.
                logger.exception(
                    "CallbackQueue: pending callback raised an exception"
                )
            invoked += 1
        return invoked

    def clear(self) -> None:
        """Discard every pending callback without invoking it.

        Used during addon shutdown to release main-thread work that no
        consumer will ever drain.
        """
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def pending(self) -> int:
        """Return the approximate number of callbacks waiting in the queue.

        Backed by :meth:`queue.Queue.qsize`, which the standard library
        documents as approximate; suitable for diagnostics only.
        """
        return self._queue.qsize()
