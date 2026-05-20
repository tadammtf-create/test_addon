"""Tencent Hunyuan3D-2 provider adapter for the AI Toolkit Platform.

This module is the bpy-free service-layer adapter that wraps Tencent's
Hunyuan3D-2 image-to-3D model exposed as a public Gradio Space. It
implements the
:class:`~ai_toolkit.services.providers.base.AIProvider` surface for the
``image_to_3d`` task and is the migration target for the legacy
:mod:`ai_toolkit.hunyuan3d_api` module.

Migration plan
--------------
The existing :mod:`ai_toolkit.hunyuan3d_api` module is preserved on disk
per Requirement 3.8 (the migration plan explicitly forbids deleting
legacy code; we wrap it instead). The legacy module did two things:

1. Called ``gradio_client.Client.predict`` against a Hunyuan3D-2 Gradio
   Space, walking the result for a ``.glb`` filesystem path.
2. Imported that path into the active Blender scene with
   ``bpy.ops.import_scene.gltf(filepath=...)``.

Step 1 is what this provider does. Step 2 is **deliberately removed**:
no module under ``services/`` may import any Blender-distributed module
(Requirement 13.1) and the Hunyuan3D-2 provider must not call
``bpy.ops.import_scene.*``. The UI-side ``AssetImporter`` (task 14.2)
handles the actual import on the main thread once the executor
dispatches the success callback.

Endpoint
--------
The Tencent Hunyuan3D-2 Gradio Space exposes a synchronous prediction
endpoint at ``api_name="/generation_all"`` that accepts an image input
(wrapped via :func:`gradio_client.file`) and returns either a single
filesystem path string or a tuple/list whose first element is the GLB
path and whose remaining elements are auxiliary files (normal map,
textures, etc.). Authentication is optional; an HF token is honoured
for higher rate limits but unauthenticated calls are accepted.

Lazy imports
------------
:mod:`gradio_client` is imported inside :meth:`submit_job` so that an
addon installation that has not yet ``pip install``-ed the package can
still register without crashing (Requirement 12.11). A clean
:class:`RuntimeError` is raised on first use instead.

For testability, the provider exposes a ``gradio_client`` injection
seam through its constructor: a callable
``factory(space_id, hf_token) -> client`` may be supplied to bypass the
real package entirely. This is what makes the unit tests in
``services/tests/test_providers/test_hunyuan3d.py`` runnable without
the network and without :mod:`gradio_client` installed.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
from typing import Any, Callable, Optional

from ..models.requests import ImageTo3DRequest
from ..models.responses import GenerationResponse
from .base import AIProvider, CredentialField


__all__ = ["Hunyuan3DProvider"]


_logger = logging.getLogger("ai_toolkit")


# Recognised 3D mesh file extensions returned by the Gradio Space. The
# public ``tencent/Hunyuan3D-2`` Space ships GLB by default; ``.gltf``
# is accepted for forwards compatibility because both formats land in
# Blender's ``import_scene.gltf`` operator on the UI side.
_VALID_MESH_EXTS: frozenset[str] = frozenset({".glb", ".gltf"})


class Hunyuan3DProvider(AIProvider):
    """Provider adapter for Tencent's Hunyuan3D-2 Gradio Space.

    Routes the ``image_to_3d`` task through a public Gradio Space.
    Declares one optional ``hf_token`` credential, declared via
    :meth:`get_required_credentials` so the AddonPreferences UI shell
    renders a single masked input row (Requirements 3.9, 14.1). The
    Gradio Space accepts unauthenticated calls; the token is for rate-
    limit relief only.
    """

    PROVIDER_ID: str = "hunyuan3d"
    DISPLAY_NAME: str = "Tencent Hunyuan3D-2 (Gradio Space)"
    SUPPORTED_TASKS: tuple[str, ...] = ("image_to_3d",)

    # Default Gradio Space identifier. The legacy code called
    # ``Jbowyer/Hunyuan3D-2.1``; the official Tencent Space at
    # ``tencent/Hunyuan3D-2`` is the more durable choice. Tests
    # override this via the constructor without touching the network.
    DEFAULT_GRADIO_SPACE: str = "tencent/Hunyuan3D-2"

    # Total time budget for one prediction call. Mesh generation is
    # heavy and the public Space queues; the legacy code passed
    # ``timeout=300`` to its underlying httpx kwargs and we mirror that.
    DEFAULT_TIMEOUT_S: float = 300.0

    # How often the cancel poll wakes up while waiting on the prediction
    # future. A 0.5 s cadence keeps the cancel responsive without
    # burning CPU on the worker thread.
    POLL_INTERVAL_S: float = 0.5

    def __init__(
        self,
        *,
        gradio_client: Any | None = None,
        gradio_space: str = DEFAULT_GRADIO_SPACE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Construct a Hunyuan3DProvider.

        Args:
            gradio_client: Optional injection seam used by tests to
                bypass :mod:`gradio_client` entirely. Two shapes are
                accepted:

                * a callable factory ``(space_id: str, hf_token:
                  str | None) -> client`` that returns an object with
                  a ``predict(image=..., api_name=...)`` method;
                * an already-built client object exposing the same
                  ``predict`` method, in which case the same client is
                  reused for every call regardless of credentials.

                When ``None`` (the default), :mod:`gradio_client` is
                imported lazily inside :meth:`submit_job` and a real
                ``gradio_client.Client`` is constructed per call.
            gradio_space: Identifier of the Gradio Space to call. The
                public Tencent Space at :attr:`DEFAULT_GRADIO_SPACE`
                works for the majority of users; advanced users can
                pin to a fork via the AddonPreferences UI in a future
                task.
            timeout_s: Per-request timeout in seconds, propagated to
                :mod:`httpx` via ``Client(... httpx_kwargs={"timeout":
                timeout_s})`` when the real client is built. Defaults
                to :attr:`DEFAULT_TIMEOUT_S`.
        """
        self._gradio_client_seam: Any | None = gradio_client
        self._gradio_space: str = gradio_space
        self._timeout_s: float = float(timeout_s)

    # ------------------------------------------------------------------
    # AIProvider interface
    # ------------------------------------------------------------------

    def get_id(self) -> str:
        """Return the registry key for this provider (Requirements 3.1, 3.10)."""
        return self.PROVIDER_ID

    def get_display_name(self) -> str:
        """Return the human-readable name shown in dropdowns (Requirement 14.5)."""
        return self.DISPLAY_NAME

    def get_supported_tasks(self) -> tuple[str, ...]:
        """Return the Task Identifiers this provider handles (Requirement 3.3)."""
        return self.SUPPORTED_TASKS

    def get_required_credentials(self) -> tuple[CredentialField, ...]:
        """Declare the credentials the AddonPreferences must render.

        The Hunyuan3D-2 Gradio Space accepts unauthenticated calls, so
        the single field is **optional**. When supplied, the token is
        forwarded to the Gradio client to lift the public rate limit
        (Requirements 3.9, 14.1).
        """
        return (
            CredentialField(
                key="hf_token",
                label="Hugging Face Token (optional)",
                is_password=True,
                description=(
                    "Optional HF token for higher rate limits when calling "
                    "the public Hunyuan3D Gradio Space."
                ),
            ),
        )

    def validate_credentials(self, credentials: dict) -> bool:
        """Return ``True`` unconditionally.

        The Hunyuan3D-2 Gradio Space is reachable without credentials,
        so no structural check is necessary: an empty ``credentials``
        dict and one carrying an ``hf_token`` are both accepted. The
        Test Connection operator (task 23.2) is responsible for live
        validation against the Space; this validator is purely a
        structural gate kept consistent with the rest of the provider
        contract (Requirements 3.12, 14.3, 14.4).

        Note: returning ``True`` here is intentional. Reviewers should
        treat the unconditional ``return True`` as the documented
        behaviour, not a typo.
        """
        # ``credentials`` is unused on purpose -- see the docstring.
        del credentials
        return True

    def submit_job(
        self,
        task: str,
        request: Any,
        credentials: dict,
        on_status: Callable[[str, dict], None],
        cancel_event: threading.Event,
    ) -> GenerationResponse:
        """Generate a 3D model with Hunyuan3D-2 and return its file path.

        See the docstring on :meth:`AIProvider.submit_job` for the
        cross-provider contract. This implementation:

        1. validates ``task == "image_to_3d"`` (raises :class:`ValueError`);
        2. validates ``request`` is an :class:`ImageTo3DRequest` whose
           ``image_path`` is a non-empty string (raises
           :class:`TypeError` / :class:`ValueError`);
        3. extracts an optional ``hf_token`` from ``credentials``;
        4. lazily imports :mod:`gradio_client` (raises a clean
           :class:`RuntimeError` if the package is missing) unless the
           constructor was given an injection seam;
        5. emits ``request_started`` through ``on_status``;
        6. runs ``client.predict(image=file(image_path),
           api_name="/generation_all")`` on a single-worker thread pool
           so ``cancel_event`` can interrupt the wait inside
           :attr:`POLL_INTERVAL_S` seconds;
        7. resolves the primary path from a ``str``, ``tuple``, or
           ``list`` result, validates it exists on disk and has a
           recognised mesh extension;
        8. emits ``model_ready`` and returns a
           :class:`GenerationResponse` whose ``output_file_paths``
           tuple starts with the primary path and is followed by any
           auxiliary files the Space returned (normal map, textures).

        Critically, this method **never** imports any Blender module:
        the returned path is handed back to the executor as-is and the
        UI-side :class:`AssetImporter` performs the
        ``bpy.ops.import_scene.gltf`` call on the main thread
        (Requirements 3.8, 13.1).
        """
        if task != "image_to_3d":
            raise ValueError(
                f"Hunyuan3DProvider does not support task {task!r}; "
                f"supported tasks: {self.SUPPORTED_TASKS!r}"
            )
        if not isinstance(request, ImageTo3DRequest):
            raise TypeError(
                "Hunyuan3DProvider expects an ImageTo3DRequest; got "
                f"{type(request).__name__}"
            )
        image_path = request.image_path
        if not isinstance(image_path, str) or not image_path.strip():
            raise ValueError(
                "Hunyuan3DProvider requires a non-empty image_path on the request"
            )

        hf_token: Optional[str] = None
        if credentials:
            raw = credentials.get("hf_token")
            if isinstance(raw, str) and raw.strip():
                hf_token = raw.strip()

        client, gr_file = self._build_client(hf_token)

        # Notify the dispatcher that we have begun the prediction call.
        # Job id is filled in by the executor wrapper; providers pass
        # an empty string and the executor splices the real id in.
        _safe_emit(on_status, "", {"event": "request_started"})

        result = self._predict_with_cancel(
            client=client,
            gr_file=gr_file,
            image_path=image_path,
            cancel_event=cancel_event,
        )

        primary_path, additional_paths = self._resolve_paths(result)

        _safe_emit(
            on_status,
            "",
            {"event": "model_ready", "path": primary_path},
        )

        return GenerationResponse(
            output_file_paths=(primary_path, *additional_paths),
            metadata={
                "provider": self.PROVIDER_ID,
                "task": task,
                "gradio_space": self._gradio_space,
            },
            provider_message="",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_client(
        self, hf_token: Optional[str]
    ) -> tuple[Any, Callable[[str], Any]]:
        """Resolve the Gradio client and ``file`` helper for this call.

        Two paths exist:

        * The constructor was given an injection seam (a factory or a
          pre-built client). In that case :mod:`gradio_client` is **not**
          imported, and ``file`` becomes the identity function so test
          stubs can ignore the wrapping. This is the path exercised by
          the unit tests.
        * No seam was given: import :mod:`gradio_client` lazily,
          construct a real :class:`Client` bound to
          :attr:`_gradio_space` with the per-call ``hf_token``, and use
          the package's :func:`gradio_client.file` helper to wrap the
          local image path.

        Lazy importing here is what lets the addon register cleanly on
        a fresh installation that has not yet run
        ``pip install gradio_client`` (Requirement 12.11). The error
        message is phrased to point the reader at the install step.
        """
        seam = self._gradio_client_seam
        if seam is not None:
            if callable(seam) and not hasattr(seam, "predict"):
                # Factory: build a fresh client per call so a per-call
                # token override can take effect.
                client = seam(self._gradio_space, hf_token)
            else:
                # Pre-built client: reuse it for every call regardless
                # of credentials. Tests primarily use this path.
                client = seam
            return client, _identity_file

        try:
            from gradio_client import Client, file as gr_file  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "gradio_client is not installed; install it to use Hunyuan3D-2"
            ) from exc

        # ``Client`` accepts ``hf_token=None`` for unauthenticated
        # access; the legacy adapter passed ``httpx_kwargs={"timeout":
        # 300.0}`` to lift the default timeout, which we preserve.
        try:
            client = Client(
                self._gradio_space,
                hf_token=hf_token,
                httpx_kwargs={"timeout": self._timeout_s},
            )
        except TypeError:
            # Older gradio_client versions do not accept
            # ``httpx_kwargs``; fall back to the basic constructor so
            # the provider still works on a downlevel install.
            client = Client(self._gradio_space, hf_token=hf_token)

        return client, gr_file

    def _predict_with_cancel(
        self,
        *,
        client: Any,
        gr_file: Callable[[str], Any],
        image_path: str,
        cancel_event: threading.Event,
    ) -> Any:
        """Run ``client.predict(...)`` while polling ``cancel_event``.

        The prediction call runs inside a single-worker thread pool so
        the outer worker thread can wake up every
        :attr:`POLL_INTERVAL_S` seconds, observe ``cancel_event``, and
        return early with a :class:`RuntimeError`.

        The pool is created per-call and torn down with
        ``shutdown(wait=False)`` on cancel so a still-running prediction
        does not delay the cooperative cancel by its full timeout. The
        leftover daemon worker thread is reaped when the process exits
        or its underlying network call completes; this is the standard
        trade-off for cancelling code that owns a blocking socket read.

        Any exception raised by ``client.predict`` is wrapped in a
        :class:`RuntimeError` so the executor can surface a useful
        :attr:`~ai_toolkit.services.jobs.job.Generation_Job.failure_reason`
        (Requirements 3.7, 4.10). The original exception is chained via
        ``raise ... from`` so the underlying cause is recoverable from
        logs.
        """
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                client.predict,
                image=gr_file(image_path),
                api_name="/generation_all",
            )
            while True:
                if cancel_event.is_set():
                    # We cannot abort a blocking socket read from
                    # outside, but we can stop waiting on it. The
                    # daemon worker thread will be reaped when its
                    # request finishes or the process exits.
                    future.cancel()
                    raise RuntimeError("Cancelled before completion")
                try:
                    return future.result(timeout=self.POLL_INTERVAL_S)
                except concurrent.futures.TimeoutError:
                    continue
                except Exception as exc:
                    raise RuntimeError(
                        f"Hunyuan3D-2 request failed: {exc}"
                    ) from exc
        finally:
            # ``wait=False`` so a cancel does not block on a still-
            # running prediction. On the success path the worker has
            # already returned, so the shutdown is effectively a no-op.
            pool.shutdown(wait=False)

    @staticmethod
    def _resolve_paths(result: Any) -> tuple[str, tuple[str, ...]]:
        """Resolve the primary mesh path and any auxiliary paths.

        The Gradio Space returns one of:

        * a single ``str`` filesystem path (the ``.glb`` itself);
        * a ``tuple`` or ``list`` whose first element is the ``.glb``
          path and whose remaining elements are auxiliary files.

        Any other shape is rejected with a :class:`RuntimeError`. The
        primary path must exist on disk and have a recognised mesh
        extension (``.glb`` or ``.gltf``); a returned ``.png`` -- the
        symptom of a Space that mis-routed the request -- is rejected
        explicitly so the executor surfaces a clean failure reason
        rather than an obscure bpy import error later in the pipeline.
        """
        if isinstance(result, str):
            primary = result
            additional: tuple[str, ...] = ()
        elif isinstance(result, (tuple, list)):
            if not result:
                raise RuntimeError(
                    "Hunyuan3D-2 returned an empty result"
                )
            primary_candidate = result[0]
            if not isinstance(primary_candidate, str):
                raise RuntimeError(
                    "Hunyuan3D-2 returned a non-string primary path: "
                    f"{type(primary_candidate).__name__}"
                )
            primary = primary_candidate
            additional = tuple(p for p in result[1:] if isinstance(p, str))
        else:
            raise RuntimeError(
                "Hunyuan3D-2 returned an unexpected result type: "
                f"{type(result).__name__}"
            )

        if not primary or not isinstance(primary, str):
            raise RuntimeError(
                "Hunyuan3D-2 returned an empty primary path"
            )

        if not os.path.isfile(primary):
            raise RuntimeError(
                f"Hunyuan3D-2 primary path does not exist on disk: {primary}"
            )

        ext = os.path.splitext(primary)[1].lower()
        if ext not in _VALID_MESH_EXTS:
            raise RuntimeError(
                f"Hunyuan3D returned unexpected file type: {primary}"
            )

        return primary, additional


def _identity_file(path: str) -> str:
    """Identity wrapper used when a test injection seam is provided.

    The real :func:`gradio_client.file` wraps a local path so the
    Gradio Space can identify it as a binary upload. Test stubs do not
    need that wrapper, so when the constructor was given an injection
    seam we hand the path through unchanged.
    """
    return path


def _safe_emit(
    on_status: Callable[[str, dict], None],
    job_id: str,
    payload: dict,
) -> None:
    """Invoke ``on_status`` and swallow any exception it raises.

    Callback exceptions must never propagate back into a provider
    (Requirements 13.8, 15.9). The executor wraps callbacks too, but
    isolating here keeps a misbehaving dispatcher from poisoning the
    Gradio exchange.
    """
    try:
        on_status(job_id, payload)
    except Exception:  # pragma: no cover - defensive
        _logger.exception(
            "Hunyuan3DProvider on_status callback raised; ignoring."
        )
