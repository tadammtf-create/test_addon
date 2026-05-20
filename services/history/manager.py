"""History entry data type and Generation_Job -> HistoryEntry mapping.

This module is part of the **Service Layer** and therefore MUST NOT import
any Blender-distributed module (``bpy``, ``bpy_extras``, ``mathutils``,
``bgl``, ``gpu``, ``bmesh``, ``blf``). Per Requirement 13.1 the entire
service layer is built and tested in a plain Python interpreter; the
History Manager is on the path that records every terminal job to local
storage, so keeping this module ``bpy``-free is what allows the
property-based tests in task 6.4-6.8 to run without Blender installed.

Four pieces compose the History Manager, all landed by tasks 6.1, 6.2,
and 6.3 in this file:

* :class:`HistoryEntry` (task 6.1) -- a frozen, primitives-only dataclass
  capturing every field listed in Requirement 10.2 (``job_id``, ``task``,
  ``provider_id``, ``prompt``, ``input_references``,
  ``output_file_paths``, ``status``, ``created_at_iso8601``,
  ``completed_at_iso8601``).
* :func:`build_history_entry` (task 6.1) -- a small factory that maps a
  terminal :class:`Generation_Job` onto a :class:`HistoryEntry`. It reads
  the prompt and input references from the request payload via
  best-effort ``getattr`` lookups so the factory works for every request
  dataclass in ``services/models/requests.py`` -- and any future ones --
  without importing them.
* :class:`HistoryManager` (task 6.2) -- the JSON-on-disk store with the
  500-entry cap, atomic writes via ``history.json.tmp`` + :func:`os.replace`,
  and the ``record`` / ``list_entries`` / ``delete`` /
  ``archive_conversation`` operations defined in Requirements 8.10, 10.1,
  10.3, 10.5-10.7, and 10.9. The remote-sync backend (Req 10.8) is
  explicitly out of scope for the initial release per Req 17.5/17.6 --
  this module is the local-storage path only.
* :func:`reimport_entry` (task 6.3) -- the pure-logic helper that, given
  a :class:`HistoryEntry` (and optionally a UI-side ``AssetImporter``),
  returns a :class:`ReimportPlan` listing the file paths to re-import or
  a :class:`ReimportError` explaining why the entry cannot be re-imported
  (Req 10.4, 10.10). The function does not call ``bpy``; the actual
  imports happen on the UI side from the returned plan.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List

from ..jobs.job import Generation_Job, JobStatus


__all__ = [
    "HistoryEntry",
    "build_history_entry",
    "HistoryManager",
    "MAX_ENTRIES",
    "ReimportPlan",
    "ReimportError",
    "reimport_entry",
]


# Addon-wide logger configured in ``services/__init__.py``. All defensive
# log records in this module use it so they share a namespace with the
# rest of the addon.
logger = logging.getLogger("ai_toolkit")


@dataclass(frozen=True)
class HistoryEntry:
    """A persisted record of one terminal :class:`Generation_Job` (Req 10.2).

    Frozen so entries are safe to share between threads and reuse for
    re-import planning. Every field is a primitive or a tuple of
    primitives, so the entry is trivially JSON-serialisable by the
    :class:`HistoryManager` (task 6.2).

    Attributes:
        job_id: The originating ``Generation_Job.job_id`` -- a 32-character
            hex string from :func:`uuid.uuid4`.
        task: The Task_Identifier the job was submitted under, e.g.
            ``"text_to_3d"``, ``"image_to_3d"``, ``"texture_generation"``,
            ``"render_preview"``, ``"chat_completion"``,
            ``"scene_analysis"``.
        provider_id: Identifier of the :class:`AIProvider` that ran the
            job, matching ``Generation_Job.provider_id``.
        prompt: User-entered prompt text. Empty string for tasks that do
            not carry a prompt (e.g. ``scene_analysis``,
            ``image_to_3d`` requests that omit it).
        input_references: User-supplied input references collected from
            the request payload via best-effort attribute lookups
            (``image_path``, ``viewport_screenshot_path``,
            ``object_name``), in that order. Empty tuple for text-only
            tasks. Stored as a tuple so the dataclass remains hashable
            and safe to share between threads.
        output_file_paths: Absolute paths the provider wrote on disk,
            copied from ``Generation_Job.response.output_file_paths``.
            Empty tuple when the job ended in ``failed`` or
            ``cancelled`` (these terminate without a response payload),
            or when a successful response carries no files.
        status: Exactly one of ``"succeeded"``, ``"failed"``, or
            ``"cancelled"`` -- the ``.value`` of the originating job's
            :class:`JobStatus`. Non-terminal labels (``"queued"``,
            ``"running"``) cannot appear here; :func:`build_history_entry`
            rejects non-terminal jobs.
        created_at_iso8601: ``Generation_Job.created_at.isoformat()`` --
            the UTC ISO-8601 timestamp at which the job was submitted.
            Used as the sort key for history listing (Req 10.3) and for
            oldest-first trimming when the 500-entry cap is reached
            (Req 10.9).
        completed_at_iso8601: ``Generation_Job.completed_at.isoformat()``
            -- the UTC ISO-8601 timestamp at which the job reached its
            terminal status. Always populated because entries are only
            built from terminal jobs whose ``completed_at`` has been set
            by the executor.
    """

    job_id: str
    task: str
    provider_id: str
    prompt: str
    input_references: tuple[str, ...]
    output_file_paths: tuple[str, ...]
    status: str
    created_at_iso8601: str
    completed_at_iso8601: str


#: Attribute names on ``Generation_Job.request`` from which
#: :func:`build_history_entry` collects ``input_references``, in the order
#: they appear in the resulting tuple. Each attribute, when present and a
#: non-empty string, contributes one entry. This list deliberately covers
#: every request dataclass declared in ``services/models/requests.py``
#: without importing any of them, keeping the factory request-type agnostic
#: and resilient to new request types added in future tasks.
_INPUT_REFERENCE_ATTRS: tuple[str, ...] = (
    "image_path",
    "viewport_screenshot_path",
    "object_name",
)


def build_history_entry(job: Generation_Job) -> HistoryEntry:
    """Map a terminal :class:`Generation_Job` onto a :class:`HistoryEntry`.

    Reads the prompt and input references from ``job.request`` via
    best-effort :func:`getattr` lookups so this factory works for every
    request dataclass declared in ``services/models/requests.py`` -- and
    any future ones -- without importing them. Reads output paths from
    ``job.response.output_file_paths`` when the response carries that
    attribute; otherwise falls back to an empty tuple, which is the
    normal case for ``failed`` and ``cancelled`` jobs (they typically
    terminate without a response attached).

    Args:
        job: The job to record. Must be in a terminal status
            (:attr:`JobStatus.is_terminal`) with ``completed_at`` set.

    Returns:
        A fresh :class:`HistoryEntry` derived from ``job``.

    Raises:
        ValueError: If ``job.status`` is not terminal. Recording a
            non-terminal job is a programmer error: the History Manager
            contract (Req 10.1) only records jobs that have reached a
            terminal status.
        ValueError: If ``job.completed_at`` is ``None``. The executor
            populates ``completed_at`` whenever it transitions a job to
            a terminal status, so a missing value indicates a malformed
            job and is treated as a programmer error.
    """
    if not job.status.is_terminal:
        raise ValueError(
            "build_history_entry requires a terminal job, got status="
            f"{job.status.value!r}"
        )
    if job.completed_at is None:
        raise ValueError(
            "build_history_entry requires job.completed_at to be set on "
            "terminal jobs"
        )

    prompt_value = getattr(job.request, "prompt", "")
    prompt = prompt_value if isinstance(prompt_value, str) else ""

    input_refs: list[str] = []
    for attr in _INPUT_REFERENCE_ATTRS:
        value = getattr(job.request, attr, None)
        if isinstance(value, str) and value:
            input_refs.append(value)

    if job.response is not None and hasattr(job.response, "output_file_paths"):
        output_paths = tuple(job.response.output_file_paths)
    else:
        output_paths = ()

    return HistoryEntry(
        job_id=job.job_id,
        task=job.task,
        provider_id=job.provider_id,
        prompt=prompt,
        input_references=tuple(input_refs),
        output_file_paths=output_paths,
        status=job.status.value,
        created_at_iso8601=job.created_at.isoformat(),
        completed_at_iso8601=job.completed_at.isoformat(),
    )


# HistoryManager is implemented below in task 6.2.
# ReimportPlan, ReimportError, and reimport_entry are implemented at the
# bottom of this module in task 6.3.


#: Maximum number of :class:`HistoryEntry` records kept in local storage
#: at any time (Requirement 10.7). When :meth:`HistoryManager.record`
#: would push the count above this cap, the oldest entries
#: (smallest ``created_at_iso8601``) are dropped first (Requirement 10.9).
MAX_ENTRIES: int = 500


def _utcnow() -> datetime:
    """Return a timezone-aware UTC :class:`datetime` for archive timestamps.

    Used as the default ``clock`` for :class:`HistoryManager`. Pulled out
    of the constructor default so tests can substitute an injectable
    deterministic clock.
    """
    return datetime.now(timezone.utc)


def _entry_to_json_dict(entry: "HistoryEntry") -> dict:
    """Serialise a :class:`HistoryEntry` to a plain JSON-safe dict.

    Tuple fields (``input_references``, ``output_file_paths``) are
    converted back to ``list`` because JSON has no tuple representation;
    :func:`_entry_from_json_dict` re-tuples them on read so the in-memory
    invariants of :class:`HistoryEntry` (frozen, hashable, immutable) are
    preserved across the round-trip.
    """
    return {
        "job_id": entry.job_id,
        "task": entry.task,
        "provider_id": entry.provider_id,
        "prompt": entry.prompt,
        "input_references": list(entry.input_references),
        "output_file_paths": list(entry.output_file_paths),
        "status": entry.status,
        "created_at_iso8601": entry.created_at_iso8601,
        "completed_at_iso8601": entry.completed_at_iso8601,
    }


def _entry_from_json_dict(raw: dict) -> "HistoryEntry":
    """Construct a :class:`HistoryEntry` from a JSON-decoded dict.

    Re-tuples the list fields and validates field types just enough to
    fail loud if the on-disk file has been hand-edited into something
    that is no longer a HistoryEntry shape. Type errors raise
    :class:`TypeError` or :class:`KeyError`, both of which are caught by
    :meth:`HistoryManager._load_from_disk` and treated as a corrupt
    file, leaving the in-memory store empty.
    """
    return HistoryEntry(
        job_id=str(raw["job_id"]),
        task=str(raw["task"]),
        provider_id=str(raw["provider_id"]),
        prompt=str(raw["prompt"]),
        input_references=tuple(str(p) for p in raw["input_references"]),
        output_file_paths=tuple(str(p) for p in raw["output_file_paths"]),
        status=str(raw["status"]),
        created_at_iso8601=str(raw["created_at_iso8601"]),
        completed_at_iso8601=str(raw["completed_at_iso8601"]),
    )


class HistoryManager:
    """JSON-on-disk persistence for :class:`HistoryEntry` records.

    The manager owns one local-storage file (passed in as
    ``storage_path``) plus an in-memory list mirror that is the single
    source of truth between writes. Every public mutator
    (:meth:`record`, :meth:`delete`, :meth:`archive_conversation`)
    modifies the in-memory list and then re-writes the file atomically
    via the ``history.json.tmp`` + :func:`os.replace` dance, so a crash
    mid-write can never leave the on-disk file in a half-written state
    (Requirement 10.1).

    A single :class:`threading.Lock` guards both the in-memory list and
    the file write so the executor's worker threads (which call
    :meth:`record` from
    :class:`~ai_toolkit.services.jobs.executor.JobExecutor` on every
    terminal status) and the Blender main thread (which calls
    :meth:`delete` and :meth:`archive_conversation` from operators) do
    not race.

    Per Requirement 13.1, this class is strictly ``bpy``-free. The
    storage path is computed UI-side using
    ``bpy.utils.user_resource('CONFIG')`` and passed in as a plain
    string; the service layer never imports ``bpy``.

    The remote-history-sync backend (Requirement 10.8) is deferred to a
    future release (Requirements 17.5, 17.6); this implementation
    handles only the local-storage path.
    """

    #: Re-exported on the class for convenience so callers can write
    #: ``HistoryManager.MAX_ENTRIES`` without importing the module-level
    #: constant separately.
    MAX_ENTRIES: int = MAX_ENTRIES

    def __init__(
        self,
        storage_path: str,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        """Construct a manager bound to ``storage_path``.

        On construction, any existing entries at ``storage_path`` are
        loaded into memory. A missing file is *not* an error (it is the
        normal first-run state); a parse error or a wrong-shaped JSON
        document logs an error and leaves the in-memory store empty so
        the addon never fails to register on a corrupt history file.

        Args:
            storage_path: Absolute filesystem path to ``history.json``.
                The temporary write file lives at
                ``<storage_path>.tmp``; conversation archive files
                (written by :meth:`archive_conversation`) live in the
                same directory under the name
                ``conversation-<job_id>.json``.
            clock: Callable returning a timezone-aware UTC
                :class:`datetime`. Injected for tests; defaults to
                :func:`datetime.datetime.now` in the UTC timezone. Used
                only by :meth:`archive_conversation` to populate the
                synthetic entry's ``created_at`` and ``completed_at``
                timestamps.
        """
        self._storage_path = storage_path
        self._clock = clock
        self._lock = threading.Lock()
        # In-memory mirror of the on-disk file. The single source of
        # truth between writes; ``list_entries`` returns a sorted copy.
        self._entries: List[HistoryEntry] = []
        self._load_from_disk()

    @property
    def storage_path(self) -> str:
        """Absolute path of the JSON file backing this manager."""
        return self._storage_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, entry: HistoryEntry) -> None:
        """Append ``entry`` and persist atomically (Requirements 10.1, 10.7, 10.9).

        Appends to the in-memory list and, if the resulting count
        exceeds :data:`MAX_ENTRIES`, drops the entries with the smallest
        ``created_at_iso8601`` until exactly :data:`MAX_ENTRIES` remain.
        This implements the "oldest first when trimming" rule of
        Requirement 10.9 -- the 500 most recent entries by
        ``created_at_iso8601`` are kept, the rest are dropped.

        After trimming, the entire list is written to ``storage_path``
        atomically via the temporary-file-and-rename idiom. Trimming and
        write together complete in well under the 2-second budget of
        Requirement 10.1 because the list never exceeds 501 entries at
        the trim point and the JSON document for 500 entries is on the
        order of a few hundred kilobytes.

        Thread-safe: concurrent ``record`` / ``delete`` /
        ``archive_conversation`` calls from worker threads and the main
        thread are serialised by the manager's internal lock.

        Args:
            entry: The :class:`HistoryEntry` to append. Built typically
                via :func:`build_history_entry` from a terminal
                :class:`Generation_Job`, but the manager itself does not
                validate the entry's contents -- any well-formed
                :class:`HistoryEntry` is accepted.
        """
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > MAX_ENTRIES:
                # Sort ascending by created_at_iso8601 then keep the
                # tail (largest dates). O(n log n) per record is fine
                # because n <= MAX_ENTRIES + 1 == 501 at this point.
                self._entries.sort(key=lambda e: e.created_at_iso8601)
                self._entries = self._entries[-MAX_ENTRIES:]
            self._write_to_disk_locked()

    def list_entries(self) -> List[HistoryEntry]:
        """Return all recorded entries sorted by ``created_at_iso8601`` desc (Req 10.3).

        Returns a fresh list copy on every call so callers can iterate
        and mutate safely without races against concurrent
        :meth:`record` / :meth:`delete` calls. Sort order is
        ``created_at_iso8601`` descending: most recent first.

        ISO-8601 timestamps with the same offset (the manager always
        writes UTC ``+00:00``) sort lexicographically in the same order
        as chronologically, so a plain string sort suffices and avoids
        a per-call :func:`datetime.fromisoformat` parse for every entry.
        """
        with self._lock:
            return sorted(
                self._entries,
                key=lambda e: e.created_at_iso8601,
                reverse=True,
            )

    def delete(self, job_id: str) -> bool:
        """Remove the entry with ``job_id`` and persist atomically (Req 10.5, 10.6).

        Removes only the index entry from local storage. Output files
        on disk are deliberately left untouched so a delete does not
        destroy the user's generated artefacts (Requirement 10.5).

        Args:
            job_id: The :attr:`HistoryEntry.job_id` of the entry to
                remove.

        Returns:
            ``True`` if an entry with that ``job_id`` existed and was
            removed, ``False`` otherwise. The caller (typically the
            History panel's delete operator) uses the ``False`` return
            to surface the "no history entry to delete" error of
            Requirement 10.6.
        """
        with self._lock:
            for index, existing in enumerate(self._entries):
                if existing.job_id == job_id:
                    del self._entries[index]
                    self._write_to_disk_locked()
                    return True
            return False

    def archive_conversation(self, messages: list) -> str:
        """Archive a chat conversation as a synthetic history entry (Req 8.10).

        Used when the user activates the AI Assistant "New Conversation"
        control: the existing conversation is archived to local storage
        as a ``chat_archive`` entry, then the active conversation is
        cleared by the UI layer.

        The serialised conversation lives in a *second* file alongside
        ``history.json``, named ``conversation-<job_id>.json``. The
        history entry itself records the path of that file in
        :attr:`HistoryEntry.output_file_paths`, so a future re-import
        operator can find the archived conversation by reading the
        history entry alone.

        Args:
            messages: The conversation messages to archive. Typically a
                list of plain dicts (``{"role": ..., "content": ...,
                ...}``) or :class:`ChatMessage` dataclass instances --
                the JSON serialiser falls back to ``__dict__`` for
                dataclass-like values and ``str`` for everything else,
                so any pickleable-ish payload round-trips.

        Returns:
            The ``job_id`` of the new history entry (a fresh
            ``uuid4().hex``). Callers can use this to immediately fetch
            the archive path via :meth:`list_entries` if needed.
        """
        now = self._clock()
        now_iso = now.isoformat()
        job_id = uuid.uuid4().hex

        archive_dir = os.path.dirname(self._storage_path) or "."
        archive_path = os.path.join(archive_dir, f"conversation-{job_id}.json")

        # Serialise messages first; if this fails, surface the error to
        # the caller so the active conversation isn't cleared on a
        # half-broken archive. Successful write happens before the
        # entry is recorded so the entry never points at a missing file.
        payload = json.dumps(
            messages,
            default=lambda o: getattr(o, "__dict__", str(o)),
            indent=2,
            ensure_ascii=False,
        )
        self._atomic_write_text(archive_path, payload)

        entry = HistoryEntry(
            job_id=job_id,
            task="chat_archive",
            provider_id="",
            prompt="",
            input_references=(),
            output_file_paths=(archive_path,),
            status="succeeded",
            created_at_iso8601=now_iso,
            completed_at_iso8601=now_iso,
        )
        self.record(entry)
        return job_id

    # ------------------------------------------------------------------
    # Internal: load / write
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> None:
        """Populate ``self._entries`` from ``storage_path``.

        Missing file is *not* an error (it is the normal first-run
        state); :attr:`self._entries` is left as the empty list set up
        by the constructor. Any other read or parse failure is caught,
        logged with the path and the exception class, and treated as an
        empty store -- this is what Requirement 12.9-style "log and
        continue" looks like for the history file specifically, and is
        what stops a corrupt history file from blocking addon
        registration.
        """
        if not os.path.exists(self._storage_path):
            return

        try:
            with open(self._storage_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, list):
                raise TypeError(
                    f"history file root must be a JSON list, got {type(raw).__name__}"
                )
            loaded = [_entry_from_json_dict(item) for item in raw]
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            logger.error(
                "HistoryManager: failed to load %s (%s: %s); "
                "starting with an empty in-memory store",
                self._storage_path,
                type(exc).__name__,
                exc,
            )
            self._entries = []
            return

        self._entries = loaded

    def _write_to_disk_locked(self) -> None:
        """Persist ``self._entries`` atomically. Caller must hold ``self._lock``.

        Writes the entire JSON document to ``<storage_path>.tmp``,
        flushes and fsyncs, then renames over ``storage_path`` via
        :func:`os.replace`. Atomicity is what guarantees Requirement
        10.1's "persist within 2 seconds" without risking a half-written
        file if the process is killed mid-write.
        """
        payload = json.dumps(
            [_entry_to_json_dict(e) for e in self._entries],
            indent=2,
            sort_keys=False,
            ensure_ascii=False,
        )
        try:
            self._atomic_write_text(self._storage_path, payload)
        except OSError as exc:
            logger.error(
                "HistoryManager: failed to write %s (%s: %s); "
                "in-memory store is ahead of disk until next successful write",
                self._storage_path,
                type(exc).__name__,
                exc,
            )

    @staticmethod
    def _atomic_write_text(target_path: str, payload: str) -> None:
        """Write ``payload`` to ``target_path`` atomically.

        The classic temporary-file-and-rename idiom: write to
        ``target_path + ".tmp"`` first, flush + fsync to push the bytes
        to the disk cache, close, then :func:`os.replace` over the
        target. On POSIX :func:`os.replace` is atomic on the same
        filesystem; on Windows it is atomic for files that are not
        currently open elsewhere, which is the case here.

        Failure to remove a leftover ``.tmp`` from a previous crashed
        write is non-fatal: :func:`os.replace` will overwrite it on the
        next successful write.

        Args:
            target_path: Absolute path of the file to (re)write.
            payload: UTF-8-encodable text content to write.

        Raises:
            OSError: If the temporary write or the final rename fails.
                The caller decides whether to log or propagate.
        """
        tmp_path = target_path + ".tmp"
        # ``open(..., "w")`` truncates if the tmp file is left over from
        # a previous failed write.
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # ``fsync`` is best-effort: some filesystems (e.g. some
                # network shares) raise here. The atomicity guarantee
                # of ``os.replace`` is what actually matters; flushing
                # the Python-level buffer before the rename is the
                # important part and ``flush()`` already did that.
                pass
        os.replace(tmp_path, target_path)


# reimport_entry is implemented below in task 6.3.


@dataclass(frozen=True)
class ReimportPlan:
    """The actions a UI-side ``AssetImporter`` should perform to re-import an entry.

    Returned by :func:`reimport_entry` when the entry is re-importable
    (status ``"succeeded"``, every output file present on disk, and any
    optional importer-side ``accepts`` gate satisfied). The UI-side
    history operator (task 21.2) reads the plan and dispatches the
    actual ``bpy.ops.import_scene.*`` calls on the main thread; this
    module never imports ``bpy`` itself.

    ``file_paths`` is the surviving subset of ``entry.output_file_paths``
    -- in the current contract this is exactly the entry's full path
    tuple, since :func:`reimport_entry` returns a :class:`ReimportError`
    if any file is missing or rejected. The field is kept on the plan
    (rather than re-derived from ``entry``) so the UI can iterate the
    paths directly without re-touching the entry.
    """

    entry: HistoryEntry
    file_paths: tuple[str, ...]


@dataclass(frozen=True)
class ReimportError:
    """The reason an entry cannot be re-imported (Req 10.10).

    Returned by :func:`reimport_entry` when any precondition for
    re-import fails. The UI surfaces a generic notification per
    Requirement 10.10; this dataclass carries enough structured detail
    for tests, logs, and a future expanded UI to distinguish the cases
    without re-running the precondition check.

    Attributes:
        entry: The :class:`HistoryEntry` that was inspected.
        reason: A short tag identifying which precondition failed.
            Always one of:

            * ``"not_succeeded"`` -- ``entry.status`` is not the literal
              string ``"succeeded"`` (Req 10.4: only succeeded entries
              are re-importable; ``"failed"``, ``"cancelled"``, and the
              chat-archive synthetic entries are filtered here, though
              chat-archive entries themselves use ``"succeeded"``).
            * ``"no_output_files"`` -- ``entry.output_file_paths`` is
              empty, so there is nothing to re-import.
            * ``"files_missing"`` -- at least one referenced output
              file is absent from disk OR rejected by the optional
              ``asset_importer.accepts(path)`` gate. Reusing one tag
              across both cases keeps the contract simple: from the
              helper's point of view, both states mean "these paths
              cannot drive a re-import".
        missing_paths: For ``"files_missing"`` only, the tuple of
            offending paths in their original order. Empty for the
            other two reasons.
    """

    entry: HistoryEntry
    reason: str
    missing_paths: tuple[str, ...] = ()


def reimport_entry(
    entry: HistoryEntry,
    asset_importer: object | None = None,
) -> ReimportPlan | ReimportError:
    """Return a re-import plan when the entry is re-importable, else an error reason.

    Pure logic: this function does **not** actually import anything. The
    UI-side ``AssetImporter`` (task 14.2) reads the returned plan and
    dispatches to ``bpy.ops.import_scene.*`` on the main thread. Per
    Requirement 13.1 the service layer never imports ``bpy``, which is
    what allows the property-based test for re-import preconditions
    (Property 35, Requirements 10.4 and 10.10) to run in a plain Python
    interpreter on hosts where Blender is not installed.

    The ``asset_importer`` argument is accepted for API parity with
    task 21.2's history operator, which forwards the live
    ``AssetImporter`` instance here so the helper can perform an
    early-out check on whether the importer would even accept any of
    the recorded paths. For now we use it only when it is supplied
    *and* exposes a callable ``accepts(path)`` method; otherwise the
    accepts gate is silently skipped. This keeps task 6.3 implementable
    today without depending on the as-yet-unwritten AssetImporter from
    task 14.2 -- Property 35 covers the full precondition set
    (status, presence on disk) regardless of whether the importer is
    wired in.

    Args:
        entry: The :class:`HistoryEntry` to inspect.
        asset_importer: Optional UI-side asset importer. When supplied
            with a callable ``accepts(path) -> bool`` method, every
            output path is run through ``accepts`` and a rejected path
            is treated the same way as a missing one (the plan cannot
            run, so the helper returns a :class:`ReimportError` with
            reason ``"files_missing"`` and the rejected paths in
            ``missing_paths``). Any other object -- ``None``, an
            instance without ``accepts``, or one whose ``accepts``
            attribute is not callable -- skips the gate silently.

    Returns:
        :class:`ReimportPlan` -- with ``file_paths`` equal to
        ``entry.output_file_paths`` -- when, in order:

        1. ``entry.status == "succeeded"`` (Req 10.4),
        2. ``entry.output_file_paths`` is non-empty,
        3. ``os.path.isfile(p)`` is true for every ``p`` in
           ``entry.output_file_paths`` (Req 10.4), and
        4. when an ``asset_importer`` with a callable ``accepts``
           method is supplied, ``asset_importer.accepts(p)`` is
           truthy for every ``p``.

        :class:`ReimportError` otherwise, with ``reason`` set to
        ``"not_succeeded"``, ``"no_output_files"``, or
        ``"files_missing"`` per the rules above (Req 10.10).
    """
    # Step 1: status gate. Req 10.4 says "succeeded" entries only;
    # comparing against the literal string keeps the helper independent
    # of the JobStatus enum (which already serialises to the same
    # string anyway, see services/jobs/job.py::JobStatus.SUCCEEDED).
    if entry.status != "succeeded":
        return ReimportError(entry=entry, reason="not_succeeded")

    # Step 2: must reference at least one file. An entry with no
    # output files cannot drive a re-import even if its status is
    # ``succeeded`` -- this happens for the synthetic chat-archive
    # entries (which carry a single archive path) and would be a bug
    # for any other terminal job, but the helper handles it
    # defensively so callers get a clear ``no_output_files`` reason
    # instead of a degenerate empty plan.
    if not entry.output_file_paths:
        return ReimportError(entry=entry, reason="no_output_files")

    # Step 3: every referenced file must exist on disk (Req 10.4).
    # Walk in order so the returned ``missing_paths`` tuple preserves
    # the entry's original ordering, which makes failure messages and
    # tests easier to read.
    missing: list[str] = [
        path for path in entry.output_file_paths if not os.path.isfile(path)
    ]
    if missing:
        return ReimportError(
            entry=entry,
            reason="files_missing",
            missing_paths=tuple(missing),
        )

    # Step 4: optional accepts-gate. Only run when the caller supplied
    # an importer that exposes a callable ``accepts`` method. Anything
    # else (None, a stub object, an attribute that is not callable)
    # skips silently -- this is what lets the helper be useful today,
    # before task 14.2 lands the real AssetImporter, while still
    # supporting the full precondition contract once it does.
    if asset_importer is not None:
        accepts = getattr(asset_importer, "accepts", None)
        if callable(accepts):
            rejected: list[str] = [
                path for path in entry.output_file_paths if not accepts(path)
            ]
            if rejected:
                # Reuse the ``files_missing`` tag so the UI can surface
                # one generic "cannot re-import" message per Req 10.10
                # without having to branch on the precise reason.
                return ReimportError(
                    entry=entry,
                    reason="files_missing",
                    missing_paths=tuple(rejected),
                )

    # All gates passed: emit the plan with the entry's full path tuple.
    return ReimportPlan(
        entry=entry,
        file_paths=tuple(entry.output_file_paths),
    )
