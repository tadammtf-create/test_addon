"""OpenAI Chat Completions provider for the AI Toolkit Platform.

This module is the bpy-free service-layer adapter that wraps OpenAI's
``/v1/chat/completions`` endpoint and serves as the *reference
implementation* for streaming-capable providers. It is the first of
several chat backends that may eventually live alongside it under
``services/providers/``.

Provider contract
-----------------
:class:`ChatOpenAIProvider` implements
:class:`~ai_toolkit.services.providers.base.AIProvider` for the single
Task Identifier ``"chat_completion"`` (Requirements 3.1, 3.8). It
accepts a :class:`~ai_toolkit.services.models.requests.ChatCompletionRequest`
and returns a
:class:`~ai_toolkit.services.models.responses.ChatCompletionResponse`
whose ``assistant_message`` is the fully-assembled assistant reply.

The provider declares one required credential (``api_key``) plus an
optional ``organization`` field, both surfaced in the AddonPreferences
shell as labelled input rows (Requirement 3.9). ``validate_credentials``
performs a structural check only; the live network probe belongs to the
Test Connection operator (task 23.2 / Requirement 14.3).

Streaming protocol
------------------
When the request's ``stream`` flag is true, the adapter speaks OpenAI's
Server-Sent-Events (SSE) chat-completions stream:

* the HTTP body arrives as a sequence of newline-separated lines; each
  data-bearing line begins with the literal prefix ``"data: "``;
* a single line of ``"data: [DONE]"`` marks the end of the stream and
  carries no payload;
* every other ``"data: ..."`` line is a JSON document with shape
  ``{"choices": [{"delta": {"content": "<text>"}}]}``; the empty-string
  case and missing ``content`` keys are valid (the provider may emit a
  delta carrying only role information at the start of the stream);
* blank lines, comment lines beginning with ``":"``, and unrecognised
  ``"event: ..."`` / ``"id: ..."`` framing are ignored.

For each non-empty content delta the provider does two things in order:

1. appends the delta text to a local buffer that becomes the final
   assistant message; and
2. emits a callback event ``{"event": "token", "delta": <text>}`` so
   the modal :class:`AITK_OT_job_dispatcher` can append the streamed
   token to the in-progress assistant message in the chat panel
   (Requirement 8.8).

Streaming token emits MUST go through the ``on_status`` callback. The
executor wraps the callback so that the canonical ``job_id`` is
spliced in; this provider passes the empty string ``""`` for the
``job_id`` argument per the wrapper convention agreed in task 4.2.

Cancel-event polling cadence
----------------------------
The provider polls ``cancel_event`` between every ``iter_lines`` tick
(streaming path) and once before / after the synchronous POST
(non-streaming path). When the event is set the provider raises
:class:`RuntimeError` with the message ``"cancelled"``; the executor's
worker loop downgrades the job to status ``cancelled`` (or
``failed`` with reason ``"cancelled"``) and surfaces the result through
the dispatcher (Requirement 4.7).

A 0.1-second tick (:attr:`POLL_INTERVAL_S`) is tighter than the
half-second cadence used by image providers because tokens stream in
quickly; we want a cancel to land between two consecutive tokens
rather than block the user behind a multi-second image fetch.

Endpoint
--------
The default base URL is ``https://api.openai.com/v1``; the chat
completions path is appended as ``/chat/completions``. The default
model is ``gpt-4o-mini``. Both are class attributes, also accepted as
constructor keyword arguments so a unit test or self-hosted deployment
can swap in a stub URL or alternate model.

The bpy-free invariant
----------------------
Per Requirement 13.1, this module imports nothing from ``bpy``,
``bpy_extras``, ``mathutils``, ``bgl``, ``gpu``, ``bmesh``, or ``blf``.
The top-level ``import requests`` is fine: ``requests`` is a third-party
HTTP library, not a Blender module, and is already a transitive
dependency of the addon via the legacy provider modules.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

import requests

from ..models.requests import ChatCompletionRequest
from ..models.responses import ChatCompletionResponse
from .base import AIProvider, CredentialField


__all__ = ["ChatOpenAIProvider"]


_logger = logging.getLogger("ai_toolkit")


# Sentinel that marks the end of an OpenAI SSE chat-completions stream.
# Lines with this exact payload (after the ``"data: "`` prefix has been
# stripped) carry no delta and signal that no more events will follow.
_DONE_SENTINEL: str = "[DONE]"

# SSE framing prefix used by OpenAI's chat-completions stream. Every
# data-bearing line begins with this six-character prefix; blank lines
# and lines beginning with ``":"`` are SSE comments and are ignored.
_DATA_PREFIX: str = "data: "


class ChatOpenAIProvider(AIProvider):
    """Provider adapter for OpenAI's Chat Completions endpoint.

    Routes the ``chat_completion`` task through ``/v1/chat/completions``
    on the configured ``base_url``. Supports both streaming and non-
    streaming request modes; streaming emits per-token callback events
    in addition to assembling the final assistant message
    (Requirement 8.8).
    """

    PROVIDER_ID: str = "chat_openai"
    DISPLAY_NAME: str = "OpenAI Chat Completions"
    SUPPORTED_TASKS: tuple[str, ...] = ("chat_completion",)

    DEFAULT_BASE_URL: str = "https://api.openai.com/v1"
    DEFAULT_MODEL: str = "gpt-4o-mini"
    DEFAULT_TIMEOUT_S: float = 120.0

    # Cancel-event polling cadence in seconds. Tighter than the half-
    # second cadence used by image providers (FluxV1, FluxV2) because
    # tokens stream quickly and a cancel should land between adjacent
    # tokens rather than after the next blocking socket read of a
    # multi-second image fetch (Requirement 4.7).
    POLL_INTERVAL_S: float = 0.1

    def __init__(
        self,
        *,
        http_session: Any | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Construct a ChatOpenAIProvider.

        Args:
            http_session: An object exposing a ``post(url, *, json,
                headers, stream, timeout)`` callable that returns an
                object with ``status_code``, ``text``, ``json()``, and
                (when ``stream=True``) ``iter_lines(decode_unicode=...)``
                attributes -- the surface of a
                :class:`requests.Session` instance. When ``None``, the
                top-level :mod:`requests` module is used directly.
                Injecting a stub here is what makes this provider unit
                testable without the network.
            base_url: Base URL of the OpenAI-compatible endpoint. The
                chat-completions path is appended as
                ``/chat/completions``. Defaults to
                :attr:`DEFAULT_BASE_URL`.
            model: Model identifier to send in the request body.
                Defaults to :attr:`DEFAULT_MODEL`.
            timeout_s: Per-request HTTP timeout in seconds, passed
                through to ``http_session.post``. Defaults to
                :attr:`DEFAULT_TIMEOUT_S`.
        """
        self._http_session = http_session if http_session is not None else requests
        # Normalise the base URL by stripping trailing slashes so the
        # appended ``/chat/completions`` path joins cleanly regardless
        # of whether the caller wrote ``"https://api.openai.com/v1"``
        # or ``"https://api.openai.com/v1/"``.
        self._base_url = base_url.rstrip("/")
        self._model = model
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
        """Declare the credentials the AddonPreferences must render.

        Two fields:

        * ``api_key`` (required, masked) -- sent as
          ``Authorization: Bearer <api_key>``. Without it the provider
          refuses to submit (Requirement 3.9).
        * ``organization`` (optional, plain text) -- sent as the
          ``OpenAI-Organization`` header for users who route requests
          through a particular OpenAI organization. The provider
          ignores this field when missing or empty so the optional
          nature is purely cosmetic in the UI.
        """
        return (
            CredentialField(
                key="api_key",
                label="OpenAI API Key",
                is_password=True,
                description=(
                    "API key used as 'Authorization: Bearer ...' for the "
                    "OpenAI Chat Completions endpoint."
                ),
            ),
            CredentialField(
                key="organization",
                label="OpenAI Organization (optional)",
                is_password=False,
                description="Optional 'OpenAI-Organization' header value.",
            ),
        )

    def validate_credentials(self, credentials: dict) -> bool:
        """Return ``True`` iff ``api_key`` is a non-empty string.

        Per the contract on :meth:`AIProvider.validate_credentials`,
        this method MUST NOT raise on missing or empty values
        (Requirements 3.12, 14.3, 14.4). A live network probe is the
        responsibility of the Test Connection operator (task 23.2);
        this validator is purely structural.
        """
        if not credentials:
            return False
        api_key = credentials.get("api_key")
        return isinstance(api_key, str) and bool(api_key.strip())

    def submit_job(
        self,
        task: str,
        request: Any,
        credentials: dict,
        on_status: Callable[[str, dict], None],
        cancel_event: threading.Event,
    ) -> ChatCompletionResponse:
        """Run a chat completion and return the final assistant message.

        See the docstring on :meth:`AIProvider.submit_job` for the
        cross-provider contract. This implementation:

        1. validates ``task == "chat_completion"`` (raises
           :class:`ValueError`);
        2. validates ``request`` is a :class:`ChatCompletionRequest`
           or duck-typed equivalent with ``messages`` and ``stream``
           attributes (raises :class:`TypeError`);
        3. requires a non-empty ``api_key`` (raises
           :class:`RuntimeError`);
        4. assembles the OpenAI-shaped ``messages`` list, prepending a
           system message that carries the optional
           ``request.scene_context`` when supplied (Requirement 8.7);
        5. emits ``{"event": "request_started"}`` and POSTs the body
           to ``<base_url>/chat/completions`` with the appropriate
           ``stream`` flag;
        6. for streaming requests, iterates the SSE response line by
           line, emits one ``{"event": "token", "delta": <text>}``
           event per content delta (Requirement 8.8), and polls
           ``cancel_event`` between iterations;
        7. for non-streaming requests, parses the JSON response and
           emits a single ``{"event": "completed"}`` event;
        8. surfaces non-200 responses as :class:`RuntimeError` so the
           executor can attach the message to ``failure_reason``
           (Requirements 3.7, 4.10).
        """
        if task != "chat_completion":
            raise ValueError(
                f"ChatOpenAIProvider does not support task {task!r}; "
                f"supported tasks: {self.SUPPORTED_TASKS!r}"
            )

        # Duck-type the request: we accept the canonical
        # :class:`ChatCompletionRequest` plus any object with the same
        # surface so a future request-type refactor is not blocked by
        # an isinstance check. The test for ``messages`` covers both
        # the tuple-of-ChatMessage and an empty tuple; the
        # ``scene_context`` field is allowed to be absent (treated as
        # ``None``) and ``stream`` is required.
        if not _looks_like_chat_completion_request(request):
            raise TypeError(
                "ChatOpenAIProvider expects a ChatCompletionRequest; got "
                f"{type(request).__name__}"
            )

        api_key = credentials.get("api_key") if credentials else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError("Missing api_key credential")
        organization = credentials.get("organization") if credentials else None

        messages_list = self._build_messages(request)
        body = {
            "model": self._model,
            "messages": messages_list,
            "stream": bool(request.stream),
        }

        headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        }
        if isinstance(organization, str) and organization.strip():
            headers["OpenAI-Organization"] = organization.strip()

        url = f"{self._base_url}/chat/completions"

        # Notify the dispatcher that we have begun the HTTP exchange.
        # Job id is filled in by the executor wrapper; providers pass
        # an empty string and the executor splices the real id in.
        _safe_emit(on_status, "", {"event": "request_started"})

        if bool(request.stream):
            assistant_message = self._run_streaming(
                url=url,
                headers=headers,
                body=body,
                on_status=on_status,
                cancel_event=cancel_event,
            )
        else:
            assistant_message = self._run_non_streaming(
                url=url,
                headers=headers,
                body=body,
                cancel_event=cancel_event,
            )
            _safe_emit(on_status, "", {"event": "completed"})

        return ChatCompletionResponse(
            assistant_message=assistant_message,
            metadata={
                "provider": self.PROVIDER_ID,
                "model": self._model,
                "stream": bool(request.stream),
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(self, request: Any) -> list[dict]:
        """Assemble the OpenAI-shaped ``messages`` list.

        Each entry in ``request.messages`` becomes a dict with
        ``{"role": m.role, "content": m.content}``. When
        ``request.scene_context`` is a non-empty dict, a system
        message carrying a JSON-serialised dump of the dict is
        prepended so the LLM sees the scene context first
        (Requirement 8.7). The serialisation uses tight separators so
        the system message stays compact in the request payload.
        """
        messages_list: list[dict] = []

        scene_context = getattr(request, "scene_context", None)
        if isinstance(scene_context, dict) and scene_context:
            messages_list.append(
                {
                    "role": "system",
                    "content": (
                        "Current Blender scene context (read-only): "
                        + json.dumps(scene_context, separators=(",", ":"))
                    ),
                }
            )

        for m in request.messages:
            messages_list.append({"role": m.role, "content": m.content})

        return messages_list

    def _run_streaming(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: dict,
        on_status: Callable[[str, dict], None],
        cancel_event: threading.Event,
    ) -> str:
        """Execute the request in streaming mode and return the assistant message.

        Polls ``cancel_event`` between every ``iter_lines`` tick. Each
        SSE ``"data: ..."`` line is parsed for a ``choices[0].delta.content``
        text delta; each non-empty delta is appended to a local buffer
        AND emitted as ``{"event": "token", "delta": <text>}`` through
        ``on_status`` (Requirement 8.8). The buffer accumulated across
        the stream is the final assistant message.
        """
        try:
            response = self._http_session.post(
                url,
                json=body,
                headers=headers,
                stream=True,
                timeout=self._timeout_s,
            )
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI Chat Completions request failed: {exc}"
            ) from exc

        self._raise_for_response(response)

        buffer: list[str] = []
        # ``iter_lines(decode_unicode=True)`` yields ``str`` on most
        # ``requests`` versions; some older bindings still yield
        # ``bytes`` even with ``decode_unicode=True``. We normalise
        # both at the top of the loop so the SSE parser only ever
        # sees ``str``.
        for raw_line in response.iter_lines(decode_unicode=True):
            # Cancel polled before processing each line so a cancel
            # signalled mid-stream interrupts within roughly one
            # token's worth of work.
            if cancel_event.is_set():
                raise RuntimeError("cancelled")

            line = _decode_line(raw_line)
            if not line:
                # SSE blank line separates events; nothing to parse.
                continue
            if line.startswith(":"):
                # SSE comment line; OpenAI uses ``": OPENROUTER PROCESSING"``
                # and similar keepalives. Ignore.
                continue
            if not line.startswith(_DATA_PREFIX):
                # Non-data SSE framing (``event:``, ``id:``, etc.)
                # Ignored: we only care about the data channel.
                continue

            payload_str = line[len(_DATA_PREFIX):]
            if payload_str == _DONE_SENTINEL:
                # End-of-stream sentinel; no more events will follow.
                break

            delta_text = _extract_content_delta(payload_str)
            if delta_text:
                buffer.append(delta_text)
                _safe_emit(
                    on_status,
                    "",
                    {"event": "token", "delta": delta_text},
                )

        # One last cancel check after the stream has drained so a
        # cancel signalled exactly at end-of-stream still surfaces as
        # the canonical ``cancelled`` error rather than as a
        # successful empty completion.
        if cancel_event.is_set():
            raise RuntimeError("cancelled")

        return "".join(buffer)

    def _run_non_streaming(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: dict,
        cancel_event: threading.Event,
    ) -> str:
        """Execute the request in non-streaming mode and return the assistant message.

        Polls ``cancel_event`` immediately before and after the
        synchronous POST so a cancel signalled adjacent to the request
        does not race with the response parser. The provider does not
        spawn a worker thread for non-streaming requests because there
        is no incremental output to interrupt; the executor's outer
        watchdog handles the worst-case multi-minute hang via the
        600-second timeout (Requirement 15.5).
        """
        if cancel_event.is_set():
            raise RuntimeError("cancelled")

        try:
            response = self._http_session.post(
                url,
                json=body,
                headers=headers,
                stream=False,
                timeout=self._timeout_s,
            )
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI Chat Completions request failed: {exc}"
            ) from exc

        if cancel_event.is_set():
            raise RuntimeError("cancelled")

        self._raise_for_response(response)

        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI Chat Completions response was not valid JSON: {exc}"
            ) from exc

        # The OpenAI shape is
        # ``{"choices": [{"message": {"content": "..."}, ...}]}``;
        # missing intermediate keys raise the same RuntimeError so a
        # malformed-but-200 response surfaces as a clean failure.
        try:
            choices = payload["choices"]
            assistant_message = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "OpenAI Chat Completions response missing choices[0].message.content: "
                f"{exc}"
            ) from exc

        if not isinstance(assistant_message, str):
            raise RuntimeError(
                "OpenAI Chat Completions response choices[0].message.content "
                f"was not a string: {type(assistant_message).__name__}"
            )

        return assistant_message

    @staticmethod
    def _raise_for_response(response: Any) -> None:
        """Raise :class:`RuntimeError` when ``response`` is not 200 OK.

        The error message includes the status code and the first 200
        characters of the response body so the executor can attach a
        useful failure reason without flooding the logs with a
        multi-megabyte HTML error page (Requirements 3.7, 4.10).
        """
        status = getattr(response, "status_code", None)
        if status == 200:
            return
        text = getattr(response, "text", "") or ""
        # Some response stubs expose ``text`` lazily; coerce to ``str``
        # defensively so a missing ``text`` attribute does not
        # short-circuit the error path.
        if not isinstance(text, str):
            text = str(text)
        snippet = text[:200]
        raise RuntimeError(
            f"OpenAI API returned {status}: {snippet}"
        )


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _looks_like_chat_completion_request(request: Any) -> bool:
    """Return ``True`` when ``request`` quacks like a ChatCompletionRequest.

    A canonical :class:`ChatCompletionRequest` is accepted directly;
    duck-typed instances are accepted when they expose ``messages``
    (an iterable, typically a tuple of :class:`ChatMessage`) and
    ``stream`` (a bool). The optional ``scene_context`` attribute is
    not required: callers may legitimately omit it.
    """
    if isinstance(request, ChatCompletionRequest):
        return True
    if not hasattr(request, "messages") or not hasattr(request, "stream"):
        return False
    # ``messages`` must be iterable; calling ``iter`` here would
    # consume a generator, so we test for ``__iter__`` instead so the
    # downstream ``for m in request.messages`` loop sees a fresh
    # iterable.
    return hasattr(request.messages, "__iter__")


def _decode_line(raw: Any) -> str:
    """Normalise a streamed SSE line to ``str``.

    ``requests.Response.iter_lines(decode_unicode=True)`` returns
    ``str`` on modern ``requests`` releases; older builds still yield
    ``bytes`` even with ``decode_unicode=True``. We accept either and
    return a stripped ``str`` so the SSE parser only ever sees text.
    """
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - decode("replace") cannot raise
            return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


def _extract_content_delta(payload_str: str) -> str:
    """Pull ``choices[0].delta.content`` out of an SSE data payload.

    Returns the empty string when the payload is malformed JSON, when
    the JSON has no ``choices`` array, when the first choice has no
    ``delta``, or when the delta has no ``content`` text. The empty
    string is the natural skip signal for the streaming loop because
    the only legitimate non-empty value is a token to append.
    """
    try:
        payload = json.loads(payload_str)
    except (ValueError, TypeError):
        _logger.debug(
            "ChatOpenAIProvider: dropping non-JSON SSE payload: %r",
            payload_str[:120],
        )
        return ""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if not isinstance(content, str):
        return ""
    return content


def _safe_emit(
    on_status: Callable[[str, dict], None],
    job_id: str,
    payload: dict,
) -> None:
    """Invoke ``on_status`` and swallow any exception it raises.

    Callback exceptions must never propagate back into a provider
    (Requirements 13.8, 15.9). The executor wraps callbacks too, but
    isolating here keeps a misbehaving dispatcher from poisoning the
    streaming exchange mid-token.
    """
    try:
        on_status(job_id, payload)
    except Exception:  # pragma: no cover - defensive
        _logger.exception(
            "ChatOpenAIProvider on_status callback raised; ignoring."
        )
