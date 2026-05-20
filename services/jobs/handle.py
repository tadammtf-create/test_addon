"""JobHandle: opaque token for a submitted Generation_Job.

A :class:`JobHandle` is the small, opaque return value of
:meth:`JobExecutor.submit <ai_toolkit.services.jobs.executor.JobExecutor.submit>`
held by the UI layer after submitting work. It carries the
:attr:`~JobHandle.job_id` and a back-reference to the
:class:`~ai_toolkit.services.jobs.executor.JobExecutor` that owns the
job; it does *not* directly expose the underlying
:class:`~ai_toolkit.services.jobs.job.Generation_Job` instance, so the
UI layer cannot accidentally mutate executor-internal state.

The handle exposes the three operations required by the service-layer
submission contract (Requirement 13.3):

* :attr:`JobHandle.job_id` -- the string job identifier.
* :meth:`JobHandle.status` -- a status-query operation.
* :meth:`JobHandle.cancel` -- a cancellation operation.

A fourth convenience method, :meth:`JobHandle.get_response`, returns
the provider's
:class:`~ai_toolkit.services.models.responses.GenerationResponse` once
the job has reached the
:attr:`~ai_toolkit.services.jobs.job.JobStatus.SUCCEEDED` terminal
state, and ``None`` otherwise.

The handle keeps a strong reference to the executor: the executor is a
long-lived singleton owned by the addon ``__init__.py``, so retaining
it from the handle does not introduce lifecycle hazards.

Per Requirement 13.1, this module is strictly ``bpy``-free: it imports
only from the Python standard library and from sibling service-layer
modules. The forward reference to
:class:`~ai_toolkit.services.jobs.executor.JobExecutor` is resolved
lazily via ``TYPE_CHECKING`` so this module remains importable before
``executor.py`` is loaded -- the executor itself imports
:class:`JobHandle` at runtime, and a runtime cross-import would
otherwise be circular.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

# ``Generation_Job`` is the underlying state object the handle wraps but
# never directly exposes. It is imported here for symmetry with the
# rest of the ``services.jobs`` subpackage even though only ``JobStatus``
# is referenced at runtime by this module.
from .job import Generation_Job, JobStatus  # noqa: F401
from ..models.responses import GenerationResponse

if TYPE_CHECKING:
    # Imported only for type-checking to avoid a circular import:
    # ``executor.py`` imports :class:`JobHandle` at runtime to construct
    # handles inside :meth:`JobExecutor.submit`.
    from .executor import JobExecutor


__all__ = ["JobHandle"]


# Addon-wide logger configured in ``services/__init__.py``. All
# defensive log records in this module use it so they share a namespace
# with the rest of the addon.
logger = logging.getLogger("ai_toolkit")


class JobHandle:
    """Opaque token returned by :meth:`JobExecutor.submit` for one submitted job.

    The handle is the only object that crosses the UI / service
    boundary after submission. UI-layer code holds it to query status,
    cancel the job, or retrieve the final
    :class:`~ai_toolkit.services.models.responses.GenerationResponse`
    when the job succeeds.

    Attributes:
        job_id: Stable string identifier of the underlying
            :class:`~ai_toolkit.services.jobs.job.Generation_Job`,
            assigned by the executor at submission time.

    Notes:
        Instances are normally created by
        :meth:`JobExecutor.submit <ai_toolkit.services.jobs.executor.JobExecutor.submit>`
        and not constructed directly by UI code.

        The class is :func:`__slots__`-only so a stray attribute write
        on the UI side does not silently grow the handle into a
        scratchpad for unrelated state.
    """

    __slots__ = ("_job_id", "_executor")

    def __init__(self, job_id: str, executor: "JobExecutor") -> None:
        """Construct a handle for ``job_id`` owned by ``executor``.

        Args:
            job_id: The stable string identifier the executor assigned
                to the underlying :class:`Generation_Job`. Treated as
                opaque -- the handle does not validate its format.
            executor: The service-layer
                :class:`~ai_toolkit.services.jobs.executor.JobExecutor`
                singleton through which status queries, cancellations,
                and response lookups are routed. The handle keeps a
                strong reference to it.
        """
        self._job_id = job_id
        self._executor = executor

    @property
    def job_id(self) -> str:
        """Stable string identifier of the underlying ``Generation_Job``."""
        return self._job_id

    def status(self) -> JobStatus:
        """Return the current :class:`JobStatus` of the job.

        Internally delegates to
        :meth:`JobExecutor.get_status <ai_toolkit.services.jobs.executor.JobExecutor.get_status>`.

        If the executor cannot find the job (for example because the
        handle has outlived the executor's bookkeeping after addon
        unregistration, or the job was pruned), a warning is logged and
        :attr:`JobStatus.CANCELLED` is returned. This defensive path
        guarantees the UI never observes ``None`` and never has to
        special-case a missing handle; it is not the normal path.
        """
        current = self._executor.get_status(self._job_id)
        if current is None:
            logger.warning(
                "JobHandle.status: executor has no record of job_id=%r; "
                "returning CANCELLED as a defensive fallback",
                self._job_id,
            )
            return JobStatus.CANCELLED
        return current

    def cancel(self) -> None:
        """Request cancellation of the job.

        Delegates to
        :meth:`JobExecutor.cancel <ai_toolkit.services.jobs.executor.JobExecutor.cancel>`,
        which sets the job's ``cancel_event``. The call is idempotent:
        invoking :meth:`cancel` more than once, or after the job has
        already reached a terminal status, is safe and has no further
        effect.
        """
        self._executor.cancel(self._job_id)

    def get_response(self) -> Optional[GenerationResponse]:
        """Return the provider response when the job has succeeded.

        Returns:
            The :class:`GenerationResponse` produced by the provider if
            the job is in
            :attr:`~ai_toolkit.services.jobs.job.JobStatus.SUCCEEDED`,
            otherwise ``None``. The executor returns ``None`` when the
            job is unknown, has not yet reached a terminal status, or
            terminated in a non-success state (``FAILED`` or
            ``CANCELLED``). UI code should always check for ``None``
            before dereferencing the result.
        """
        return self._executor.get_response(self._job_id)

    def __repr__(self) -> str:
        """Diagnostic ``repr`` containing the job id and current status value."""
        return f"JobHandle(job_id={self.job_id!r}, status={self.status().value})"
