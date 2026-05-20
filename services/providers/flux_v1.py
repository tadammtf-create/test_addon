"""FLUX.1-dev provider adapter for the AI Toolkit Platform.

This module is the bpy-free service-layer adapter that wraps Black Forest
Labs' FLUX.1-dev model exposed through Hugging Face's Inference router.
It implements the :class:`~ai_toolkit.services.providers.base.AIProvider`
surface for the ``text_to_image`` Task Identifier and is the sibling of
:mod:`ai_toolkit.services.providers.flux_v2` -- the two adapters share a
shape on purpose so future maintainers reading either file see the same
control flow.

Migration plan
--------------
The legacy :mod:`ai_toolkit.flux_1` module is preserved on disk per
Requirement 3.8 (the migration plan explicitly forbids deleting legacy
code; we wrap it instead). The legacy module imports ``bpy`` and saves
straight to the user's desktop via the ``USERPROFILE`` environment
variable; both behaviours are unsuitable for the service layer. This
adapter therefore reimplements the same conceptual pipeline -- prompt
in, image bytes out, image bytes written to disk -- against the HF
Inference router so the call:

* never imports any Blender module (Requirement 13.1);
* writes to a parameterised output path supplied via the
  :class:`~ai_toolkit.services.models.requests.TextToImageRequest`
  rather than the hard-coded desktop path that lived in ``flux_1.py``;
* honours the cooperative ``cancel_event`` channel from the
  :class:`~ai_toolkit.services.jobs.executor.JobExecutor` so the user
  can abort within :attr:`POLL_INTERVAL_S` seconds (Requirement 4.7);
* emits status events through ``on_status`` so the modal Job Dispatcher
  can update the UI (Requirements 13.7, 15.7).

The implementation is intentionally NOT a subclass of
:class:`~ai_toolkit.services.providers.flux_v2.FluxV2Provider`. Each
provider stays a self-contained adapter so a behavioural change in one
cannot leak into the other.

Endpoint
--------
The Hugging Face Inference router exposes synchronous binary image
output for the ``black-forest-labs/FLUX.1-dev`` model at
:attr:`HF_API_URL`. The router accepts ``Authorization: Bearer <token>``
and a JSON body of the form ``{"inputs": "<prompt>", "parameters": {...}}``,
returning ``image/png`` bytes on success. When the model is loading or
the request is malformed, the router instead returns a JSON body whose
``error`` field carries the reason; this adapter surfaces that as a
:class:`RuntimeError` so the executor can attach the message to
:attr:`~ai_toolkit.services.jobs.job.Generation_Job.failure_reason`
(Requirements 3.7, 4.10).
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

import requests

from ..models.requests import TextToImageRequest
from ..models.responses import GenerationResponse
from .base import AIProvider, CredentialField


__all__ = ["FluxV1Provider"]


_logger = logging.getLogger("ai_toolkit")


class FluxV1Provider(AIProvider):
    """Provider adapter for Black Forest Labs' FLUX.1-dev model.

    Routes the ``text_to_image`` task through the Hugging Face Inference
    router. Requires a single ``hf_token`` credential, declared via
    :meth:`get_required_credentials` so the AddonPreferences UI shell
    renders one masked input row (Requirements 3.9, 14.1).
    """

    PROVIDER_ID: str = "flux_v1"
    DISPLAY_NAME: str = "FLUX.1-dev (HF Inference API)"
    SUPPORTED_TASKS: tuple[str, ...] = ("text_to_image",)

    # The Hugging Face Inference router endpoint for this model. Kept
    # as a class attribute so tests can monkeypatch it without
    # instantiating a session.
    HF_API_URL: str = (
        "https://router.huggingface.co/hf-inference/models/"
        "black-forest-labs/FLUX.1-dev"
    )

    # Total time budget for one HTTP call. 180 s is generous enough
    # that the model has time to warm up on the inference server while
    # still bounded so a stuck call can be timed out by the executor's
    # 600 s watchdog (Requirement 15.5).
    DEFAULT_TIMEOUT_S: float = 180.0

    # How often the cancel poll wakes up while waiting on the HTTP
    # future. A 0.5 s cadence keeps cancellation responsive without
    # burning CPU on the worker thread (Requirements 4.7, 15.1).
    POLL_INTERVAL_S: float = 0.5

    def __init__(
        self,
        *,
        http_session: Any | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Construct a FluxV1Provider.

        Args:
            http_session: Injectable session-like object that exposes a
                ``post(url, *, headers, json, timeout)`` callable
                returning an object with ``status_code``, ``headers``,
                ``content``, ``text``, and ``json()`` attributes -- the
                surface of a :class:`requests.Session`. When ``None``
                the top-level :mod:`requests` module is used directly.
                The seam exists so unit tests can stub the HTTP layer
                without monkey-patching the real :mod:`requests`.
            timeout_s: Per-request HTTP timeout in seconds, passed
                through to ``http_session.post``. Defaults to
                :attr:`DEFAULT_TIMEOUT_S`.
        """
        self._http_session = http_session if http_session is not None else requests
        self._timeout_s = float(timeout_s)

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
        """Declare the credentials the AddonPreferences must render (Requirement 3.9)."""
        return (
            CredentialField(
                key="hf_token",
                label="Hugging Face Access Token",
                is_password=True,
                description=(
                    "Used as 'Bearer ...' for the FLUX.1-dev Inference API."
                ),
            ),
        )

    def validate_credentials(self, credentials: dict) -> bool:
        """Return ``True`` iff ``hf_token`` is a non-empty string.

        Per the contract on
        :meth:`~ai_toolkit.services.providers.base.AIProvider.validate_credentials`,
        this method MUST NOT raise on missing or empty values
        (Requirements 3.12, 14.3, 14.4). A live network probe is the
        responsibility of the Test Connection operator (task 23.2);
        this validator is purely structural so the AddonPreferences
        Test Connection button can give immediate feedback even when
        the user is offline.
        """
        token = credentials.get("hf_token") if credentials else None
        return isinstance(token, str) and bool(token.strip())

    def submit_job(
        self,
        task: str,
        request: Any,
        credentials: dict,
        on_status: Callable[[str, dict], None],
        cancel_event: threading.Event,
    ) -> GenerationResponse:
        """Generate an image with FLUX.1-dev and return its filesystem path.

        See the docstring on
        :meth:`~ai_toolkit.services.providers.base.AIProvider.submit_job`
        for the cross-provider contract. This implementation:

        1. validates ``task == "text_to_image"`` (raises :class:`ValueError`);
        2. validates ``request`` is a :class:`TextToImageRequest`
           (raises :class:`TypeError`);
        3. requires a non-empty ``hf_token`` (raises :class:`RuntimeError`);
        4. resolves the output path from the request, falling back to a
           per-call temp file under :func:`tempfile.gettempdir`;
        5. POSTs to :attr:`HF_API_URL` on a single-worker thread pool
           so ``cancel_event`` can interrupt the wait inside
           :attr:`POLL_INTERVAL_S` seconds;
        6. validates the response (status 200, ``image/`` content-type);
           a JSON error body is surfaced as :class:`RuntimeError`;
        7. writes the bytes to disk and emits an ``image_saved`` event;
        8. returns a :class:`GenerationResponse` with a one-element
           ``output_file_paths`` tuple.

        The empty string passed to ``on_status`` as the first argument
        is intentional: per Property 38 the executor wraps the provider
        callback in :class:`JobExecutor._make_provider_status_cb` and
        rebuilds the payload with the canonical ``job_id`` before it
        reaches the Job Dispatcher, so providers do not need to know
        their own ``job_id``.
        """
        if task != "text_to_image":
            raise ValueError(
                f"FluxV1Provider does not support task {task!r}"
            )
        if not isinstance(request, TextToImageRequest):
            raise TypeError(
                "FluxV1Provider expects a TextToImageRequest; got "
                f"{type(request).__name__}"
            )

        hf_token = credentials.get("hf_token") if credentials else None
        if not isinstance(hf_token, str) or not hf_token.strip():
            raise RuntimeError("Missing hf_token credential")

        output_path = self._resolve_output_path(request)

        # Notify the dispatcher that we have begun the HTTP exchange.
        # See the docstring for why the job_id slot is left empty.
        _safe_emit(on_status, "", {"event": "request_started"})

        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {hf_token.strip()}",
            "Accept": "image/png",
        }

        response = self._post_with_cancel(
            url=self.HF_API_URL,
            headers=headers,
            payload=payload,
            cancel_event=cancel_event,
        )

        self._raise_for_response(response)

        # Persist the bytes. Wrap the write in a try/except so a disk
        # failure becomes a clean RuntimeError that the executor can
        # attach to ``failure_reason`` (Requirements 3.7, 4.10).
        try:
            with open(output_path, "wb") as fh:
                fh.write(response.content)
        except OSError as exc:
            raise RuntimeError(
                f"Failed to write FLUX.1-dev image to {output_path!r}: {exc}"
            ) from exc

        _safe_emit(
            on_status,
            "",
            {"event": "image_saved", "path": output_path},
        )

        return GenerationResponse(
            output_file_paths=(output_path,),
            metadata={"provider": self.PROVIDER_ID, "task": task},
            provider_message="",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_output_path(request: TextToImageRequest) -> str:
        """Return the path the generated PNG should be written to.

        Honours ``request.output_path`` verbatim when set to a non-
        empty string; otherwise picks a unique temp path so concurrent
        jobs cannot collide. The user's desktop is *never* touched
        from this layer -- a deliberate departure from the legacy
        :mod:`ai_toolkit.flux_1` behaviour, see the module docstring.
        """
        candidate = request.output_path
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return os.path.join(
            tempfile.gettempdir(),
            f"ai_toolkit_flux_v1_{uuid.uuid4().hex[:8]}.png",
        )

    @staticmethod
    def _build_payload(request: TextToImageRequest) -> dict:
        """Build the JSON body posted to the HF Inference router.

        ``inputs`` carries the prompt; optional FLUX parameters
        (``seed``, ``negative_prompt``) go under a ``parameters`` sub-
        dict and are dropped when ``None`` so the router sees only
        explicit overrides. The router accepts both shapes; a clean
        payload is friendlier for unit tests.
        """
        payload: dict = {"inputs": request.prompt}
        parameters: dict = {}
        if request.seed is not None:
            parameters["seed"] = request.seed
        if request.negative_prompt is not None:
            parameters["negative_prompt"] = request.negative_prompt
        if parameters:
            payload["parameters"] = parameters
        return payload

    def _post_with_cancel(
        self,
        *,
        url: str,
        headers: dict,
        payload: dict,
        cancel_event: threading.Event,
    ):
        """POST ``payload`` to ``url`` while polling ``cancel_event``.

        The HTTP call runs inside a single-worker thread pool so the
        outer worker thread can wake up every :attr:`POLL_INTERVAL_S`
        seconds and observe ``cancel_event``. We cannot abort a
        blocking socket read from outside, but we can stop waiting on
        it; the worker thread is reaped when its request finishes or
        when the process exits.

        The pool is *not* held by a ``with`` block on purpose: a
        ``with`` exit calls ``pool.shutdown(wait=True)`` which would
        block the cancel path until the in-flight ``post`` returned --
        defeating the whole point of cooperative cancellation
        (Requirements 4.7, 15.1). We shut down with ``wait=False`` in
        a ``finally`` instead so the cancel path returns within
        :attr:`POLL_INTERVAL_S` seconds.
        """
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                self._http_session.post,
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout_s,
            )
            while not future.done():
                if cancel_event.is_set():
                    future.cancel()
                    raise RuntimeError("cancelled")
                time.sleep(self.POLL_INTERVAL_S)

            # ``future.done()`` is True; ``future.result()`` returns
            # the response object on success and re-raises any
            # exception the underlying ``post`` call raised. We let
            # those bubble so the executor records
            # ``failure_reason = str(exc)`` (Requirements 3.7, 4.10).
            return future.result()
        finally:
            pool.shutdown(wait=False)

    @staticmethod
    def _raise_for_response(response: Any) -> None:
        """Validate the HF Inference response, raising on any error.

        Two failure paths produce distinct messages so the AI Assistant
        and the failure banners can show useful context:

        * non-200 status -> ``"HF Inference API returned <code>: <text[:200]>"``;
        * 200 with a JSON body (model loading, malformed prompt, ...)
          -> ``"FLUX.1 returned an error: <body['error']>"``.

        A 200 with image bytes is the only success path; everything
        else is treated as a provider failure (Requirements 3.7,
        4.10).
        """
        status = getattr(response, "status_code", None)
        if status != 200:
            text = getattr(response, "text", None)
            text_excerpt = text[:200] if isinstance(text, str) else ""
            raise RuntimeError(
                f"HF Inference API returned {status}: {text_excerpt}"
            )

        # Read the Content-Type header in a tolerant way -- tests and
        # some HTTP stacks may expose ``headers`` as a plain dict that
        # is case-sensitive.
        content_type = ""
        headers = getattr(response, "headers", None)
        if headers is not None:
            getter = getattr(headers, "get", None)
            if callable(getter):
                content_type = (
                    getter("Content-Type")
                    or getter("content-type")
                    or ""
                )
            else:
                try:
                    content_type = headers["Content-Type"]
                except (KeyError, TypeError):
                    try:
                        content_type = headers["content-type"]
                    except (KeyError, TypeError):
                        content_type = ""
        if not isinstance(content_type, str):
            content_type = ""

        if content_type.lower().startswith("image/"):
            return

        # Anything else: try to parse the body as JSON. This is the
        # documented HF error path -- the router returns 200 with
        # ``{"error": "...", "estimated_time": ...}`` while the model
        # is still loading.
        body = None
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                body = json_method()
            except Exception:
                body = None

        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(
                f"FLUX.1 returned an error: {body['error']}"
            )

        # Unrecognised shape: fall back to the response text so the
        # failure banner still has something to display.
        text = getattr(response, "text", None)
        excerpt = text[:200] if isinstance(text, str) else repr(body)
        raise RuntimeError(
            f"FLUX.1 returned an error: {excerpt}"
        )


def _safe_emit(
    on_status: Callable[[str, dict], None],
    job_id: str,
    payload: dict,
) -> None:
    """Invoke ``on_status`` and swallow any exception it raises.

    Callback exceptions must never propagate back into a provider
    (Requirements 13.8, 15.9). The executor wraps callbacks too, but
    isolating here keeps a misbehaving dispatcher from poisoning the
    HTTP exchange.
    """
    try:
        on_status(job_id, payload)
    except Exception:  # pragma: no cover - defensive
        _logger.exception(
            "FluxV1Provider on_status callback raised; ignoring."
        )
