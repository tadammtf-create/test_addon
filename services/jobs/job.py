"""Core job dataclasses for the AI Toolkit service layer.

This module defines the two fundamental data types that flow through the
job-execution pipeline:

* :class:`JobStatus` -- a five-label enum (``QUEUED``, ``RUNNING``,
  ``SUCCEEDED``, ``FAILED``, ``CANCELLED``) describing every possible
  state of a :class:`Generation_Job`, plus an :attr:`JobStatus.is_terminal`
  predicate that distinguishes the three terminal states from the two
  in-flight ones (Requirement 4.3).
* :class:`Generation_Job` -- a mutable :func:`~dataclasses.dataclass`
  carrying a single asynchronous request from submission through to
  completion. It owns the identity fields (``job_id``, ``task``,
  ``provider_id``), the request/response payloads, lifecycle status,
  UTC timestamps, and a :class:`threading.Event` used as the
  cooperative cancellation channel between submitter and worker.

Both types are pure data: no Blender (``bpy``) imports, no I/O, no
threading orchestration beyond the lone :class:`~threading.Event`.
Higher layers in ``services/jobs/`` (executor, handle, callbacks)
compose these primitives into the actual job-execution machinery.
Per Requirement 13.1 this module is strictly ``bpy``-free so it can be
unit-tested in a plain Python 3 interpreter (Requirements 13.4, 13.6).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


__all__ = ["JobStatus", "Generation_Job"]


def _utcnow() -> datetime:
    """Return a timezone-aware UTC :class:`datetime` for timestamp fields."""
    return datetime.now(timezone.utc)


def _new_job_id() -> str:
    """Return a fresh 32-character hex job identifier (``uuid4().hex``)."""
    return uuid.uuid4().hex


class JobStatus(str, Enum):
    """Lifecycle status of a :class:`Generation_Job`.

    The enum mixes in ``str`` so that members serialise cleanly to JSON
    and compare equal to their plain string form. This is convenient
    when the status crosses the UI / service boundary inside a callback
    payload as a plain string (Property 38, Requirements 13.7, 15.7).
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """``True`` iff the status admits no further transitions.

        The terminal statuses are :attr:`SUCCEEDED`, :attr:`FAILED`, and
        :attr:`CANCELLED` (Requirement 4.3). :attr:`QUEUED` and
        :attr:`RUNNING` are non-terminal.
        """
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass
class Generation_Job:
    """A single asynchronous AI generation request and its lifecycle state.

    Instances are created by
    :class:`~ai_toolkit.services.jobs.executor.JobExecutor` on every
    ``submit`` call and are mutated as the job progresses through
    :class:`JobStatus` transitions. The :attr:`cancel_event` is the
    cooperative cancellation channel: workers poll it during long-running
    provider calls and abort early when it is set
    (Requirements 4.7, 13.7, 15.1).

    All timestamp fields use timezone-aware UTC :class:`datetime` values
    (Requirement 15.7). :attr:`started_at` and :attr:`completed_at` are
    populated by the executor when the job transitions out of
    :attr:`JobStatus.QUEUED` and into a terminal status, respectively;
    they are ``None`` until then.
    """

    #: Stable identifier; 32-character hex string from :func:`uuid.uuid4`.
    job_id: str = field(default_factory=_new_job_id)
    #: Task identifier this job was submitted for, e.g. ``"text_to_image"``.
    task: str = ""
    #: Identifier of the :class:`AIProvider` that handles this job.
    provider_id: str = ""
    #: Task-specific request payload (one of the dataclasses in
    #: ``services/models/requests.py``). Typed :class:`~typing.Any` so
    #: this module does not need to import every request type.
    request: Any = None
    #: Current lifecycle status. Starts at :attr:`JobStatus.QUEUED`.
    status: JobStatus = JobStatus.QUEUED
    #: Provider response payload on successful completion; ``None`` otherwise.
    response: Any = None
    #: Free-form description of why the job failed; ``None`` while the
    #: job is in flight or succeeded.
    failure_reason: Optional[str] = None
    #: Wall-clock time at which the job was constructed (UTC).
    created_at: datetime = field(default_factory=_utcnow)
    #: Wall-clock time at which a worker began executing the job (UTC).
    #: ``None`` until the executor transitions the job to
    #: :attr:`JobStatus.RUNNING`.
    started_at: Optional[datetime] = None
    #: Wall-clock time at which the job reached a terminal status (UTC).
    #: ``None`` until then.
    completed_at: Optional[datetime] = None
    #: Cooperative cancellation channel. Set by
    #: :meth:`JobHandle.cancel <ai_toolkit.services.jobs.handle.JobHandle.cancel>`
    #: and polled by the worker. Each :class:`Generation_Job` gets its
    #: own :class:`threading.Event` (never shared between jobs).
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def short_id(self) -> str:
        """First 8 characters of :attr:`job_id`.

        Used in user-facing labels such as the AI Texturing material
        name ``<object_name>_AI_<short_id>`` (Requirement 6.7).
        """
        return self.job_id[:8]

    @property
    def elapsed_ms(self) -> int:
        """Non-negative milliseconds elapsed since the worker started.

        Returns ``0`` while :attr:`started_at` is ``None`` (i.e. the job
        is still queued and has not yet been picked up by a worker).
        Once the job has started, returns
        ``(completed_at or now) - started_at`` rounded down to whole
        milliseconds.

        The result is clamped at zero so that a non-monotonic system
        clock or a manually constructed job with mismatched timestamps
        cannot produce a negative duration. This is the value emitted
        in status-callback payloads under the ``"elapsed_ms"`` key
        (Property 38, Requirements 13.7, 15.7).
        """
        if self.started_at is None:
            return 0
        end = self.completed_at if self.completed_at is not None else _utcnow()
        delta_ms = int((end - self.started_at).total_seconds() * 1000)
        return delta_ms if delta_ms >= 0 else 0
