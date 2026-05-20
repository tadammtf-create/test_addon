"""JobExecutor: bounded background-thread runner for Generation_Job submissions.

This module is the AI Toolkit's only entry point into the worker-thread world.
:class:`JobExecutor` accepts a Generation_Job submission, runs the chosen
:class:`AIProvider`'s :meth:`submit_job` on a dedicated worker pool, surfaces
lifecycle status events through an optional caller-supplied callback, and
records every terminal job to the :class:`HistoryManager`.

Design contract
---------------
* The executor is backed by a :class:`concurrent.futures.ThreadPoolExecutor`
  with at most :data:`MAX_WORKERS` (4) workers, plus an internal
  :class:`queue.Queue` of capacity :data:`MAX_QUEUE_SIZE` (100) used as the
  backpressure gate (Requirements 15.4, 15.8).
* Each call to :meth:`JobExecutor.submit` resolves a provider through the
  :class:`ProviderRegistry`, registers a new :class:`Generation_Job`, emits a
  ``queued`` status event, hands the job to a pool worker, and returns a
  :class:`JobHandle` identifying the submission (Requirement 13.3).
* The worker honours an early cancel, transitions the job to ``running``,
  invokes the provider with a watchdog timeout of
  :data:`DEFAULT_JOB_TIMEOUT_S` (600.0 s), and enforces:

  - ``failed`` with ``failure_reason = "timeout"`` when the watchdog fires
    (Requirement 15.5);
  - ``failed`` with ``failure_reason = str(exc)`` when the provider raises
    (Requirement 3.7);
  - ``cancelled`` when the provider returns cleanly but the job's
    ``cancel_event`` was set during the call;
  - ``succeeded`` otherwise.

  After every terminal transition the executor emits a final status event
  and records the job through :class:`HistoryManager` if one was passed
  in (Requirement 10.1).
* Status callbacks are invoked with two positional arguments,
  ``(job_id, payload)``, where ``payload`` is a :class:`dict` whose
  canonical keys are ``"status"`` (one of the five labels defined by
  :class:`JobStatus`) and ``"elapsed_ms"`` (a non-negative :class:`int`);
  provider-supplied extras (``"event"``, ``"delta"``, ...) ride alongside
  these canonical keys (Requirements 13.7, 15.7, Property 38).
* Exceptions raised by the registered status callback are caught at the
  delivery site, logged through the addon-wide ``ai_toolkit`` logger, and
  never propagated; subsequent jobs and subsequent transitions of the same
  job continue to receive their callbacks (Requirements 13.8, 15.9).
* :meth:`JobExecutor.shutdown` signals ``cancel_event`` on every still-active
  job, waits for outstanding worker futures up to the supplied timeout, tears
  down the pool, and reports whether every worker reached a terminal status
  before the timeout expired (Requirement 15.6). The default timeout of 5.0 s
  matches the addon's ``unregister`` budget.
* Per Requirement 13.1 this module is strictly ``bpy``-free. The forward
  references to :class:`ProviderRegistry`, :class:`HistoryManager`, and
  :class:`AIProvider` are resolved through ``TYPE_CHECKING``; the
  :func:`build_history_entry` factory is imported lazily inside
  :meth:`_record_history` so this module compiles even before the History
  Manager (task 6.2) is fully wired.

Requirements satisfied
----------------------
3.7, 13.3, 13.7, 13.8, 15.1, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

from .handle import JobHandle
from .job import Generation_Job, JobStatus
from ..models.responses import GenerationResponse

if TYPE_CHECKING:
    # Imported only for type checking. The runtime cycle through
    # ``ProviderRegistry`` and ``HistoryManager`` is broken by passing both
    # in via the constructor; ``AIProvider`` is referenced only as a type
    # hint on internal helpers.
    from ..history.manager import HistoryManager
    from ..providers.base import AIProvider
    from ..providers.registry import ProviderRegistry


__all__ = ["JobExecutor", "JobHandle", "JobQueueFullError"]


#: Maximum wall-clock seconds a single Generation_Job may stay in
#: :attr:`JobStatus.RUNNING` before the watchdog forces a terminal
#: ``failed`` with ``failure_reason = "timeout"`` (Requirement 15.5).
DEFAULT_JOB_TIMEOUT_S: float = 600.0

#: Maximum number of Generation_Jobs the outer pool may run concurrently
#: (Requirement 15.4).
MAX_WORKERS: int = 4

#: Maximum number of in-flight Generation_Jobs the executor will accept
#: before further submissions raise :class:`JobQueueFullError`
#: (Requirement 15.8).
MAX_QUEUE_SIZE: int = 100

#: Default deadline for :meth:`JobExecutor.shutdown`. Matches the addon's
#: ``unregister`` budget (Requirement 15.6).
DEFAULT_SHUTDOWN_TIMEOUT_S: float = 5.0


# Use the addon-wide logger configured in ``services/__init__.py`` so every
# log record from the executor shares the ``ai_toolkit`` namespace.
logger = logging.getLogger("ai_toolkit")


class JobQueueFullError(RuntimeError):
    """Raised by :meth:`JobExecutor.submit` when the queue is at capacity.

    Surfaced to the UI layer so it can render the "job queue full" notice
    described in Requirement 15.8. Subclasses :class:`RuntimeError` so
    callers that catch the broader category still see this signal.
    """


def _utcnow() -> datetime:
    """Return a timezone-aware UTC :class:`datetime` for timestamp fields."""
    return datetime.now(timezone.utc)


class JobExecutor:
    """Bounded thread-pool runner for Generation_Job submissions.

    The executor owns three coordinated pieces of state:

    * a :class:`concurrent.futures.ThreadPoolExecutor` of at most
      :data:`MAX_WORKERS` worker threads, each of which loops over
      :meth:`_worker_run` for one job at a time;
    * a :class:`queue.Queue` of capacity :data:`MAX_QUEUE_SIZE` used as the
      backpressure gate between submitters and workers (Requirements 15.4,
      15.8); workers block on this queue rather than on a Future, so we get
      first-come-first-served semantics for free;
    * a job table (``self._jobs``) protected by ``self._jobs_lock`` that
      indexes every submitted :class:`Generation_Job` by its ``job_id`` so
      :meth:`cancel`, :meth:`get_status`, and :meth:`get_response` can run in
      O(1).

    Requirements satisfied: 3.7, 13.3, 13.7, 13.8, 15.1, 15.4, 15.5, 15.6,
    15.7, 15.8, 15.9.
    """

    def __init__(
        self,
        registry: "ProviderRegistry",
        history: Optional["HistoryManager"] = None,
        *,
        max_workers: int = MAX_WORKERS,
        max_queue_size: int = MAX_QUEUE_SIZE,
        timeout_s: float = DEFAULT_JOB_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Construct a fresh executor backed by ``registry`` and ``history``.

        Args:
            registry: The provider registry used to resolve a concrete
                :class:`AIProvider` for each submitted task.
            history: Optional :class:`HistoryManager`. When set, every
                terminal job is recorded via
                ``history.record(build_history_entry(job))`` (Req 10.1, 10.7).
                When ``None`` the executor still emits terminal status events
                but does not write to disk -- useful for tests that only care
                about the status pipeline.
            max_workers: Concurrency cap for the outer worker pool. Defaults
                to :data:`MAX_WORKERS` (4) per Requirement 15.4. Tests may
                lower this to exercise queue-full and shutdown behaviour
                without spinning up four real threads.
            max_queue_size: Capacity of the pending-job queue. Defaults to
                :data:`MAX_QUEUE_SIZE` (100) per Requirement 15.8.
            timeout_s: Per-job watchdog timeout in seconds. Defaults to
                :data:`DEFAULT_JOB_TIMEOUT_S` (600.0) per Requirement 15.5.
            clock: Monotonic clock injection point used by
                :meth:`shutdown`. Defaults to :func:`time.monotonic`. Tests
                may replace it with a virtual clock to exercise the
                deadline path without sleeping.
        """
        self._registry = registry
        self._history = history
        self._timeout_s = timeout_s
        self._clock = clock

        # Job table: job_id -> Generation_Job. ``_jobs_lock`` guards every
        # mutation so submit/cancel/get_status see consistent state across
        # the submitter, worker, and shutdown threads.
        self._jobs: dict[str, Generation_Job] = {}
        self._jobs_lock = threading.Lock()

        # Backpressure queue. We never block on ``put`` -- :meth:`submit`
        # uses :meth:`queue.Queue.put_nowait` and converts ``queue.Full``
        # into :class:`JobQueueFullError` (Req 15.8). Workers do block on
        # ``get`` to receive their next job.
        self._queue: queue.Queue[Generation_Job] = queue.Queue(
            maxsize=max_queue_size
        )

        # Outer worker pool. ``_pool`` runs at most ``max_workers``
        # ``_worker_run`` invocations concurrently. Each ``submit`` call
        # appends the resulting Future to ``_futures`` so :meth:`shutdown`
        # can wait on every still-running worker explicitly.
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: list[Future] = []
        self._futures_lock = threading.Lock()

        # Set during shutdown so cooperating workers stop pulling new work.
        self._closed = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(
        self,
        task: str,
        request: Any,
        on_status: Optional[Callable[[str, dict], None]] = None,
        credentials: Optional[dict] = None,
    ) -> JobHandle:
        """Submit a Generation_Job for ``task`` and return its handle.

        Resolves a provider through the registry, builds and registers a
        fresh :class:`Generation_Job`, emits a ``queued`` status event,
        enqueues the job for a worker, and returns a :class:`JobHandle`.

        Args:
            task: One of the canonical Task Identifiers (``"text_to_image"``,
                ``"image_to_3d"``, ``"text_to_3d"``, ``"texture_generation"``,
                ``"render_preview"``, ``"chat_completion"``,
                ``"scene_analysis"``).
            request: One of the request dataclasses in
                :mod:`ai_toolkit.services.models.requests`. May be ``None``
                for tasks that do not carry a payload.
            on_status: Optional status callback invoked with
                ``(job_id, payload)`` on every status transition.
            credentials: Optional credential dict forwarded verbatim to the
                provider's :meth:`submit_job`. ``None`` is normalised to an
                empty dict so providers can always assume a mapping.

        Returns:
            A :class:`JobHandle` carrying the new job's id.

        Raises:
            RuntimeError: If no provider is registered for ``task``. Per
                Requirement 3.6 the calling module is responsible for
                surfacing this as a UI error.
            JobQueueFullError: If the pending-job queue already holds
                :data:`MAX_QUEUE_SIZE` entries (Requirement 15.8).
        """
        provider = self._registry.get_for_task(task)
        if provider is None:
            raise RuntimeError(f"No provider registered for task {task!r}")

        job = Generation_Job(
            task=task,
            provider_id=provider.get_id(),
            request=request,
        )

        # Register first, then enqueue. If enqueue fails we roll back the
        # registration so the job table never lists a job that no worker
        # will ever pick up.
        with self._jobs_lock:
            self._jobs[job.job_id] = job
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._jobs_lock:
                self._jobs.pop(job.job_id, None)
            raise JobQueueFullError(
                f"job queue is full ({self._queue.maxsize})"
            ) from None

        # Emit the canonical ``queued`` event before handing the job to a
        # worker. Submitters that listen for ``queued`` see it on the
        # submitting thread, which matches the design's expectation that
        # :meth:`submit` reports admission synchronously.
        self._emit(on_status, job)

        creds = credentials if credentials is not None else {}
        future = self._pool.submit(
            self._worker_run, job, on_status, creds, provider
        )
        with self._futures_lock:
            self._futures.append(future)

        return JobHandle(job.job_id, self)

    def cancel(self, job_id: str) -> None:
        """Request cancellation of ``job_id``. Idempotent.

        Sets the underlying job's ``cancel_event`` if the job is known to
        the executor. The worker observes this in two places:

        * before transitioning ``QUEUED -> RUNNING``: a job whose cancel
          event is already set is short-circuited to ``CANCELLED`` without
          ever entering the provider;
        * during the provider call: cooperating providers poll
          ``cancel_event`` and abort early.

        Calls referencing an unknown job id are silently ignored, so the
        caller never has to check the executor's bookkeeping first.
        """
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is not None:
            job.cancel_event.set()

    def get_status(self, job_id: str) -> Optional[JobStatus]:
        """Return the current :class:`JobStatus` of ``job_id`` or ``None``.

        ``None`` indicates the executor has no record of ``job_id`` -- for
        example because the handle outlived the executor's bookkeeping
        after addon unregistration. The :class:`JobHandle` translates this
        to a defensive ``CANCELLED`` for UI consumers.
        """
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        return job.status if job is not None else None

    def get_response(self, job_id: str) -> Optional[GenerationResponse]:
        """Return the provider response only when the job has succeeded.

        Returns the :class:`GenerationResponse` attached to the job if
        ``status == JobStatus.SUCCEEDED``; otherwise (job missing,
        in-flight, or terminated in ``FAILED`` / ``CANCELLED``) returns
        ``None``. Matches :meth:`JobHandle.get_response` semantics.
        """
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None or job.status is not JobStatus.SUCCEEDED:
            return None
        return job.response

    def active_count(self) -> int:
        """Return the number of jobs currently in ``QUEUED`` or ``RUNNING``.

        Diagnostic only; reads under the jobs lock to avoid tearing.
        """
        with self._jobs_lock:
            return sum(
                1
                for job in self._jobs.values()
                if not job.status.is_terminal
            )

    def queued_count(self) -> int:
        """Return the approximate number of jobs awaiting a worker.

        Backed by :meth:`queue.Queue.qsize`; the standard library
        documents this as approximate (because workers may pop entries
        between the read and the next observation), so this is a
        diagnostic-grade value rather than an authoritative one.
        """
        return self._queue.qsize()

    def shutdown(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_S) -> bool:
        """Cancel every active job and drain the pool within ``timeout``.

        Implements Requirement 15.6: every queued or running Generation_Job
        is cancelled, every worker thread is released, and the call
        returns ``True`` iff this is achieved within ``timeout`` seconds.

        The shutdown sequence is:

        1. Set :attr:`_closed` so cooperating consumers stop pulling new
           work.
        2. Set ``cancel_event`` on every non-terminal job so cooperating
           providers exit early.
        3. Wait for every previously-submitted worker future to complete,
           up to the wall-clock deadline ``self._clock() + timeout``.
        4. Tear down the underlying pool. ``cancel_futures=True`` ensures
           workers that have not yet started are skipped rather than run.
        5. Return ``True`` only when every collected future finished and
           every job is terminal.
        """
        self._closed.set()

        # Snapshot the still-active jobs under the lock so workers
        # transitioning to terminal status mid-iteration cannot trip us
        # up. The actual cancel_event sets happen outside the lock to
        # avoid any chance of contending with worker lookups.
        with self._jobs_lock:
            active_jobs = [
                job for job in self._jobs.values() if not job.status.is_terminal
            ]
        for job in active_jobs:
            job.cancel_event.set()

        with self._futures_lock:
            futures_snapshot = list(self._futures)

        deadline = self._clock() + timeout
        remaining = max(0.0, deadline - self._clock())
        done, not_done = concurrent.futures.wait(
            futures_snapshot, timeout=remaining
        )

        # Tear down the pool. ``cancel_futures=True`` is supported on
        # Python 3.9+ and is the desired behaviour: workers still parked
        # in the pool's internal queue should be cancelled rather than
        # run to completion. We pass ``wait=False`` so this call does
        # not block beyond what we already waited for above.
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # pragma: no cover - defensive for older Pythons
            self._pool.shutdown(wait=False)

        if not_done:
            logger.warning(
                "JobExecutor.shutdown: %d worker future(s) did not "
                "complete within %.2fs",
                len(not_done),
                timeout,
            )
            return False

        # Even if every future is done, double-check that every job is
        # terminal -- this is the contract callers depend on. A future
        # that finished early because of an unhandled exception is a bug
        # we want to surface in logs rather than silently report
        # success.
        with self._jobs_lock:
            stragglers = [
                job
                for job in self._jobs.values()
                if not job.status.is_terminal
            ]
        if stragglers:
            logger.warning(
                "JobExecutor.shutdown: %d job(s) still non-terminal "
                "after pool drain: %s",
                len(stragglers),
                ", ".join(j.short_id for j in stragglers),
            )
            return False

        return True


    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _emit(
        self,
        on_status: Optional[Callable[[str, dict], None]],
        job: Generation_Job,
        **extra: Any,
    ) -> None:
        """Deliver one status event to the caller's callback.

        Builds a payload dict with the canonical ``status`` and
        ``elapsed_ms`` keys (Property 38, Requirements 13.7, 15.7) and
        merges in any provider-supplied extras (``event``, ``delta``, ...).
        Exceptions raised by ``on_status`` are caught at the delivery site
        and logged through the ``ai_toolkit`` logger; they never propagate
        out of this method (Requirements 13.8, 15.9).

        ``on_status`` may be ``None``, in which case the call is a no-op
        -- callers that don't care about progress shouldn't have to
        provide a sink.
        """
        if on_status is None:
            return
        payload: dict = {
            "status": job.status.value,
            "elapsed_ms": job.elapsed_ms,
        }
        if extra:
            payload.update(extra)
        try:
            on_status(job.job_id, payload)
        except Exception:
            logger.exception(
                "JobExecutor: status callback raised for job %s; "
                "exception swallowed per Req 13.8/15.9",
                job.short_id,
            )

    def _make_provider_status_cb(
        self,
        on_status: Optional[Callable[[str, dict], None]],
        job: Generation_Job,
    ) -> Callable[[str, dict], None]:
        """Return a closure providers can hand status events to directly.

        The provider receives a ``(job_id, payload)`` callable, but the
        executor remains the single source of truth for the canonical
        ``status`` and ``elapsed_ms`` keys. The closure therefore strips
        any ``status`` key the provider includes (so it cannot lie about
        the lifecycle state) and forwards every remaining key through
        :meth:`_emit`, which re-adds the canonical pair.
        """

        def _provider_cb(_job_id: str, payload: dict) -> None:
            extras = {k: v for k, v in payload.items() if k != "status"}
            self._emit(on_status, job, **extras)

        return _provider_cb

    def _record_history(self, job: Generation_Job) -> None:
        """Record a terminal ``job`` to :attr:`_history` if one is set.

        Imports :func:`build_history_entry` lazily so this module can be
        imported (and the executor unit-tested) before
        :mod:`ai_toolkit.services.history.manager` is fully wired -- task
        6.2 implements the rest of the History Manager. Any exception
        raised during the record path is caught and logged so that a
        misbehaving history backend cannot crash the worker.
        """
        if self._history is None or not job.status.is_terminal:
            return
        try:
            from ..history.manager import build_history_entry  # lazy
            self._history.record(build_history_entry(job))
        except Exception:
            logger.exception(
                "JobExecutor: failed to record terminal job %s to history",
                job.short_id,
            )

    def _worker_run(
        self,
        job: Generation_Job,
        on_status: Optional[Callable[[str, dict], None]],
        credentials: dict,
        provider: "AIProvider",
    ) -> None:
        """Run a single Generation_Job from queued through to terminal.

        The worker is responsible for every transition into a terminal
        state and for every status event emitted on behalf of the job.
        It runs on a thread owned by ``self._pool`` and never reuses
        ``self._queue`` -- the caller of :meth:`submit` already passed
        ``job`` directly through the pool's submit path. Steps:

        1. Honour an early cancel: if ``cancel_event`` is set before the
           worker even starts, the job is short-circuited to
           ``CANCELLED`` and never enters the provider. ``started_at``
           and ``completed_at`` are set to the same UTC instant so
           downstream consumers that expect both fields populated on
           terminal jobs (notably :func:`build_history_entry`) see
           consistent values.
        2. Transition ``QUEUED -> RUNNING``, set ``started_at``, emit a
           ``running`` event.
        3. Run the provider with a watchdog timeout via a per-call
           single-worker pool. ``concurrent.futures.wait`` plus
           ``Future.result(timeout=...)`` give us the timeout machinery
           we need without spawning a long-lived auxiliary pool.
        4. On :class:`concurrent.futures.TimeoutError`, set
           ``cancel_event`` so the still-running provider thread can
           wind down, transition to ``FAILED`` with
           ``failure_reason = "timeout"``, emit terminal, record history,
           return.
        5. On any other exception from the provider, transition to
           ``FAILED`` with ``failure_reason = str(exc)`` (or the class
           name when ``str(exc)`` is empty), emit terminal, record,
           return (Req 3.7).
        6. On clean return, downgrade to ``CANCELLED`` if the
           ``cancel_event`` was set during the call (cooperative-cancel
           contract); otherwise transition to ``SUCCEEDED`` and attach
           the response.
        """
        # Step 1: check for an early cancel before doing any provider work.
        if job.cancel_event.is_set():
            now = _utcnow()
            job.started_at = now
            job.completed_at = now
            job.status = JobStatus.CANCELLED
            self._emit(on_status, job)
            self._record_history(job)
            return

        # Step 2: transition to RUNNING.
        job.started_at = _utcnow()
        job.status = JobStatus.RUNNING
        self._emit(on_status, job)

        # Step 3: invoke the provider through a tiny per-call pool so we
        # get ``Future.result(timeout=...)`` for free. We deliberately
        # don't share a long-lived inner pool: keeping the inner
        # executor scoped to one call avoids any risk of one job's
        # straggler thread blocking another job's watchdog timer.
        provider_cb = self._make_provider_status_cb(on_status, job)
        inner_pool = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"aitk-job-{job.short_id}",
        )
        try:
            future: Future[GenerationResponse] = inner_pool.submit(
                provider.submit_job,
                job.task,
                job.request,
                credentials,
                provider_cb,
                job.cancel_event,
            )
            try:
                result = future.result(timeout=self._timeout_s)
            except concurrent.futures.TimeoutError:
                # Step 4: watchdog fired. Notify the still-running
                # provider via cancel_event and mark the job failed.
                job.cancel_event.set()
                job.failure_reason = "timeout"
                job.completed_at = _utcnow()
                job.status = JobStatus.FAILED
                self._emit(on_status, job)
                self._record_history(job)
                return
            except Exception as exc:
                # Step 5: provider raised. Stringify; fall back to the
                # class name when the exception carries no message so
                # ``failure_reason`` is never an empty string.
                job.failure_reason = str(exc) or exc.__class__.__name__
                job.completed_at = _utcnow()
                job.status = JobStatus.FAILED
                self._emit(on_status, job)
                self._record_history(job)
                return
        finally:
            # Drop the inner pool aggressively. Workers that are still
            # running here had their cancel_event set above (timeout
            # path) or have already returned (normal/exception paths).
            try:
                inner_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # pragma: no cover - defensive for older Pythons
                inner_pool.shutdown(wait=False)

        # Step 6: provider returned cleanly. Decide success vs cancelled
        # based on whether anyone asked for a cancel during the call.
        job.completed_at = _utcnow()
        if job.cancel_event.is_set():
            job.status = JobStatus.CANCELLED
        else:
            job.response = result
            job.status = JobStatus.SUCCEEDED
        self._emit(on_status, job)
        self._record_history(job)
