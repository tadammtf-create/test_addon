"""FLUX.2-dev provider adapter for the AI Toolkit Platform.

This module is the bpy-free service-layer adapter that wraps Black Forest
Labs' FLUX.2-dev model exposed through Hugging Face's Inference router. It
is the sibling of :mod:`ai_toolkit.services.providers.flux_v1` and
implements the same :class:`~ai_toolkit.services.providers.base.AIProvider`
surface, differing only in the model identifier in :attr:`HF_API_URL` and
in the provider id / display name.

Migration plan
--------------
The existing :mod:`ai_toolkit.flux_2` legacy module is preserved on disk
per Requirement 3.8 (the migration plan explicitly forbids deleting
legacy code; we wrap it instead). This adapter re-implements the same
"prompt -> image bytes -> file on disk" pipeline against the HF Inference
router so the call:

* never imports any Blender module (Requirement 13.1);
* writes to a parameterised output path supplied via the request rather
  than the desktop hard-code that lived in ``flux_2.py``;
* honours the cooperative ``cancel_event`` channel from the
  :class:`~ai_toolkit.services.jobs.executor.JobExecutor` so the user
  can abort within :attr:`POLL_INTERVAL_S` seconds (Requirement 4.7);
* emits status events through ``on_status`` so the modal Job Dispatcher
  can update the UI (Requirements 13.7, 15.7).

The implementation is intentionally NOT a subclass of
:class:`~ai_toolkit.services.providers.flux_v1.FluxV1Provider`. Each
provider stays a self-contained adapter so future maintainers can read a
single file end-to-end and so that a behavioural change in the FLUX.1
adapter cannot accidentally leak into FLUX.2.

Endpoint
--------
The Hugging Face Inference router exposes synchronous binary image
output for the ``black-forest-labs/FLUX.2-dev`` model at
:attr:`HF_API_URL`. The router accepts ``Authorization: Bearer <token>``
and a JSON body of the form ``{"inputs": "<prompt>", "parameters": {...}}``,
returning ``image/png`` bytes on success or a JSON error body on failure.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import tempfile
import threading
import uuid
from typing import Any, Callable

import requests

from ..models.requests import TextToImageRequest
from ..models.responses import GenerationResponse
from .base import AIProvider, CredentialField


__all__ = ["FluxV2Provider"]


_logger = logging.getLogger("ai_toolkit")


class FluxV2Provider(AIProvider):
    """Provider adapter for Black Forest Labs' FLUX.2-dev model.

    Routes the ``text_to_image`` task through the Hugging Face Inference
    router. Requires a single ``hf_token`` credential, declared via
    :meth:`get_required_credentials` so the AddonPreferences UI shell
    renders one masked input row (Requirements 3.9, 14.1).
    """

    PROVIDER_ID: str = "flux_v2"
    DISPLAY_NAME: str = "FLUX.2-dev (HF Inference API)"
    SUPPORTED_TASKS: tuple[str, ...] = ("text_to_image",)

    # The Hugging Face Inference router endpoint for this model. Same
    # router base as FluxV1; only the model identifier in the path
    # differs. Kept as a class attribute so tests can monkeypatch it
    # without instantiating a session.
    HF_API_URL: str = (
        "https://router.huggingface.co/hf-inference/models/"
        "black-forest-labs/FLUX.2-dev"
    )

    # Total time budget for one HTTP call. FLUX.2 is heavier than
    # FLUX.1, so we keep a generous default; callers can tighten this
    # via the constructor.
    DEFAULT_TIMEOUT_S: float = 180.0

    # How often the cancel poll wakes up while waiting on the HTTP
    # future. A 0.5 s cadence keeps the cancel responsive without
    # burning CPU on the worker thread.
    POLL_INTERVAL_S: float = 0.5

    def __init__(
        self,
        *,
        http_session: Any | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Construct a FluxV2Provider.

        Args:
            http_session: An object exposing a ``post(url, *, headers,
                json, timeout)`` callable that returns an object with
                ``status_code``, ``headers``, ``content``, and
                ``json()`` attributes -- the surface of a
                :class:`requests.Session` instance. When ``None``, the
                top-level :mod:`requests` module is used directly.
                Injecting a stub here is what makes this provider unit
                testable without the network.
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
                    "User access token from https://huggingface.co/settings/tokens "
                    "with at least 'read' permission. Required to call the "
                    "FLUX.2-dev model on the Hugging Face Inference router."
                ),
            ),
        )

    def validate_credentials(self, credentials: dict) -> bool:
        """Return ``True`` iff ``hf_token`` is a non-empty string.

        Per the contract on :meth:`AIProvider.validate_credentials`,
        this method MUST NOT raise on missing or empty values
        (Requirements 3.12, 14.3, 14.4). A live network probe is the
        responsibility of the Test Connection operator (task 23.2);
        this validator is purely structural.
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
        """Generate an image with FLUX.2-dev and return its filesystem path.

        See the docstring on :meth:`AIProvider.submit_job` for the
        cross-provider contract. This implementation:

        1. validates ``task == "text_to_image"`` (raises :class:`ValueError`);
        2. validates ``request`` is a :class:`TextToImageRequest`
           (raises :class:`TypeError`);
        3. requires a non-empty ``hf_token`` (raises :class:`RuntimeError`);
        4. resolves the output path from the request, falling back to a
           per-call temp file under :func:`tempfile.gettempdir`;
        5. POSTs to :attr:`HF_API_URL` on a single-worker thread pool so
           ``cancel_event`` can interrupt the wait inside
           :attr:`POLL_INTERVAL_S` seconds;
        6. validates the response (status 200, ``image/`` content-type);
           a JSON error body is surfaced as :class:`RuntimeError`;
        7. writes the bytes to disk and emits an ``image_saved`` event;
        8. returns a :class:`GenerationResponse` with a one-element
           ``output_file_paths`` tuple.
        """
        if task != "text_to_image":
            raise ValueError(
                f"FluxV2Provider does not support task {task!r}; "
                f"supported tasks: {self.SUPPORTED_TASKS!r}"
            )
        if not isinstance(request, TextToImageRequest):
            raise TypeError(
                "FluxV2Provider expects a TextToImageRequest; got "
                f"{type(request).__name__}"
            )

        hf_token = credentials.get("hf_token") if credentials else None
        if not isinstance(hf_token, str) or not hf_token.strip():
            raise RuntimeError("Missing hf_token credential")

        output_path = self._resolve_output_path(request)

        # Notify the dispatcher that we have begun the HTTP exchange.
        # Job id is filled in by the executor wrapper; providers pass
        # an empty string and the executor splices the real id in.
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
                f"Failed to write FLUX.2-dev image to {output_path!r}: {exc}"
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
        """Return the path to write the generated PNG to.

        Honours ``request.output_path`` when set to a non-empty string;
        otherwise picks a unique temp path so concurrent jobs cannot
        collide.
        """
        candidate = request.output_path
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return os.path.join(
            tempfile.gettempdir(),
            f"ai_toolkit_flux_v2_{uuid.uuid4().hex[:8]}.png",
        )

    @staticmethod
    def _build_payload(request: TextToImageRequest) -> dict:
        """Build the JSON body posted to the HF Inference router.

        ``inputs`` carries the prompt; optional FLUX parameters
        (``negative_prompt``, ``seed``) go under a ``parameters`` sub-
        dict and are dropped when ``None`` so the router sees only
        explicit overrides.
        """
        payload: dict = {"inputs": request.prompt}
        parameters: dict = {}
        if request.negative_prompt is not None:
            parameters["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            parameters["seed"] = request.seed
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
        seconds, observe ``cancel_event``, and return early. The pool
        is shut down with ``wait=False`` on every exit path so a
        cancel does not block the caller on an already-issued network
        request -- the underlying ``requests`` call still has its own
        timeout to bound it.
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
            while True:
                if cancel_event.is_set():
                    # We cannot abort a blocking socket read from
                    # outside, but we can stop waiting on it. The
                    # worker thread will be reaped when its request
                    # finishes (bounded by ``self._timeout_s``) or
                    # when the interpreter exits.
                    future.cancel()
                    raise RuntimeError("Cancelled before completion")
                try:
                    return future.result(timeout=self.POLL_INTERVAL_S)
                except concurrent.futures.TimeoutError:
                    continue
                except Exception as exc:  # network errors, etc.
                    raise RuntimeError(
                        f"FLUX.2-dev request failed: {exc}"
                    ) from exc
        finally:
            # ``wait=False`` so a cancel returns immediately rather
            # than blocking on the in-flight HTTP call.
            pool.shutdown(wait=False)

    @staticmethod
    def _raise_for_response(response: Any) -> None:
        """Validate the HF Inference response, raising on any error.

        A successful response has status 200 and a ``Content-Type``
        starting with ``image/``. Anything else -- including a 200 with
        a JSON body, which is how the router signals model errors --
        becomes a :class:`RuntimeError` so the executor can surface a
        useful failure reason (Requirements 3.7, 4.10).
        """
        status = getattr(response, "status_code", None)
        if status != 200:
            message = _extract_error_message(response)
            raise RuntimeError(
                f"FLUX.2-dev request failed (HTTP {status}): {message}"
            )

        content_type = ""
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                content_type = headers.get("Content-Type", "") or headers.get(
                    "content-type", ""
                )
            except AttributeError:
                content_type = ""
        if not isinstance(content_type, str) or not content_type.lower().startswith(
            "image/"
        ):
            message = _extract_error_message(response)
            raise RuntimeError(
                "FLUX.2-dev returned a non-image response "
                f"(Content-Type={content_type!r}): {message}"
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
            "FluxV2Provider on_status callback raised; ignoring."
        )


def _extract_error_message(response: Any) -> str:
    """Best-effort extraction of an error message from a failing response."""
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            body = json_method()
        except Exception:
            body = None
        if isinstance(body, dict):
            for key in ("error", "message", "detail"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return str(body)
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return repr(content)
    return "no response body"
