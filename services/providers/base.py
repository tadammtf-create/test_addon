"""Provider plug-in contract for the AI Toolkit Platform.

This module defines the two public types that every AI provider plug-in
must speak to:

* :class:`CredentialField` -- a frozen dataclass that describes a single
  credential the provider needs. The UI-side AddonPreferences renders one
  ``StringProperty`` per declared field, with ``subtype='PASSWORD'`` when
  :attr:`CredentialField.is_password` is ``True`` (Requirements 3.9, 14.1).
* :class:`AIProvider` -- the abstract base class for provider adapters.
  Each subclass wires one external AI service (FLUX, Hunyuan3D, an LLM
  chat backend, ...) into the addon's task router. The
  :class:`~ai_toolkit.services.providers.registry.ProviderRegistry`
  discovers concrete subclasses dropped into ``services/providers/`` and
  routes :class:`~ai_toolkit.services.jobs.job.Generation_Job` instances
  to the chosen provider by Task Identifier (Requirements 3.1, 3.2,
  3.3, 3.10, 3.11).

Plug-in contract
----------------
A concrete :class:`AIProvider` MUST implement every abstract method.
Instantiating a subclass that misses any abstract method raises
:class:`TypeError`, which the registry catches and logs while continuing
to load the remaining provider modules (Requirement 3.11).

* :meth:`AIProvider.get_id` returns a stable string identifier (e.g.
  ``"flux_v1"``) used as the registry key and stored in
  :attr:`~ai_toolkit.services.jobs.job.Generation_Job.provider_id`.
  IDs MUST be unique within the registry.
* :meth:`AIProvider.get_supported_tasks` returns the set of Task
  Identifiers this provider can handle, as a tuple of strings (e.g.
  ``("text_to_image",)``, ``("image_to_3d",)``).
* :meth:`AIProvider.validate_credentials` is the gate exercised by the
  Test Connection operator (Requirement 14.3). It must be safe to call
  from a worker thread and MUST NOT raise on missing or empty values:
  return ``False`` instead. Bounded network calls are permitted.
* :meth:`AIProvider.submit_job` performs the actual work on a worker
  thread. It receives a cooperative :class:`threading.Event` cancel
  channel and a status callback ``(job_id, payload)`` where ``payload``
  always carries a ``"status"`` key (Property 38, Requirements 13.7,
  15.7). On failure it raises; the executor catches the exception and
  attaches ``str(exc)`` to
  :attr:`~ai_toolkit.services.jobs.job.Generation_Job.failure_reason`
  (Requirements 3.7, 4.10, 5.10).

The bpy-free invariant
----------------------
Per Requirement 13.1, this module imports nothing from ``bpy``,
``bpy_extras``, ``mathutils``, ``bgl``, ``gpu``, ``bmesh``, or ``blf``.
It depends only on the Python standard library and on
:mod:`ai_toolkit.services.models.responses`, which is itself bpy-free.
This is what allows provider implementations to be unit-tested in a
plain Python 3 interpreter without Blender installed (Requirements
13.4, 13.6).

Concrete provider classes (``FluxV1Provider``, ``Hunyuan3DProvider``,
...) live in sibling modules under ``services/providers/`` and are
intentionally NOT registered here.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from ..models.responses import GenerationResponse


__all__ = ["AIProvider", "CredentialField"]


@dataclass(frozen=True)
class CredentialField:
    """Declarative description of one credential a provider needs.

    A provider returns a tuple of these from
    :meth:`AIProvider.get_required_credentials`. The UI-side
    AddonPreferences shell creates one ``StringProperty`` per field on
    registration and renders a labelled input row in the preferences
    panel. The persisted value is later passed back to the provider in
    the ``credentials`` dict argument of
    :meth:`AIProvider.submit_job` and
    :meth:`AIProvider.validate_credentials`.

    Attributes:
        key: Settings key suffix used to persist this credential
            (for example ``"hf_token"``). Must be unique within a single
            provider's credential list.
        label: Human-readable label rendered next to the input row in
            the AddonPreferences panel.
        is_password: When ``True`` the UI renders the property with
            ``subtype='PASSWORD'`` so the value is masked on screen.
            Defaults to ``True`` because credentials are almost always
            secrets; provider authors can opt out for non-sensitive
            fields such as a base URL or organization id
            (Requirement 14.1).
        description: Optional help text rendered as a tooltip or sub-
            label in the AddonPreferences panel. Defaults to the empty
            string.
    """

    key: str
    label: str
    is_password: bool = True
    description: str = ""


class AIProvider(ABC):
    """Abstract base class for AI provider adapters.

    Each concrete subclass wraps exactly one external AI service and
    advertises which Task Identifiers it can handle. The
    :class:`~ai_toolkit.services.providers.registry.ProviderRegistry`
    discovers subclasses dropped into ``services/providers/`` at addon
    registration time and routes
    :class:`~ai_toolkit.services.jobs.job.Generation_Job` submissions to
    the chosen provider by task (Requirements 3.1, 3.2, 3.3, 3.10).

    All methods are abstract: this class is *interface only*. Providing
    a default implementation here would silently mask incomplete
    subclasses, defeating the registry's per-module isolation guarantee
    (Requirement 3.11).
    """

    @abstractmethod
    def get_id(self) -> str:
        """Return the provider's stable string identifier.

        The identifier is the registry key (so two providers MUST NOT
        share an id) and is stored in
        :attr:`~ai_toolkit.services.jobs.job.Generation_Job.provider_id`
        so a job can be associated with the provider that produced it.
        Examples: ``"flux_v1"``, ``"flux_v2"``, ``"hunyuan3d"``,
        ``"chat_openai"`` (Requirements 3.1, 3.10).
        """

    @abstractmethod
    def get_display_name(self) -> str:
        """Return the human-readable provider name shown in dropdowns.

        Used in the per-task default-provider selectors in the addon
        preferences and in error banners. Free-form text; not required
        to be unique, though uniqueness is recommended for clarity
        (Requirement 14.5).
        """

    @abstractmethod
    def get_supported_tasks(self) -> tuple[str, ...]:
        """Return the Task Identifiers this provider can handle.

        Each entry is one of the canonical Task Identifiers documented
        in the requirements glossary: ``"text_to_image"``,
        ``"image_to_3d"``, ``"text_to_3d"``, ``"texture_generation"``,
        ``"render_preview"``, ``"chat_completion"``,
        ``"scene_analysis"``. The returned tuple drives
        :meth:`ProviderRegistry.list_for_task` and
        :meth:`ProviderRegistry.get_for_task`
        (Requirements 3.3, 3.4, 3.5, 3.10).
        """

    @abstractmethod
    def get_required_credentials(self) -> tuple[CredentialField, ...]:
        """Return the credential fields the AddonPreferences must render.

        The UI-side AddonPreferences shell uses this list at addon
        registration time to dynamically create one ``StringProperty``
        per field and to render a labelled input row in the preferences
        panel (Requirement 3.9). Providers that need no credentials
        return an empty tuple.
        """

    @abstractmethod
    def validate_credentials(self, credentials: dict) -> bool:
        """Return ``True`` iff ``credentials`` is sufficient to accept jobs.

        Called by the Test Connection operator from
        :class:`~ai_toolkit.ui.preferences` and may be invoked from a
        worker thread, so implementations MUST be thread-safe and MUST
        NOT mutate global state. They MUST NOT raise on missing or
        empty values: return ``False`` instead so the operator can
        surface a clean error message rather than a stack trace
        (Requirements 3.12, 14.3, 14.4).

        Network calls are permitted, but implementations should bound
        them with a short timeout so the Test Connection operator can
        complete within its 30-second deadline (Requirement 14.4).

        ``credentials`` is a plain :class:`dict` mapping
        :attr:`CredentialField.key` to the user-entered string value.
        """

    @abstractmethod
    def submit_job(
        self,
        task: str,
        request: Any,
        credentials: dict,
        on_status: Callable[[str, dict], None],
        cancel_event: threading.Event,
    ) -> GenerationResponse:
        """Run a Generation_Job on a worker thread and return the result.

        Called by
        :class:`~ai_toolkit.services.jobs.executor.JobExecutor` on a
        worker thread for every submitted job. Implementations MUST:

        * poll ``cancel_event`` during long-running operations and
          abort early when it is set, raising any exception or
          returning early as appropriate (Requirements 4.7, 15.1);
        * call ``on_status(job_id, payload)`` to emit progress events.
          ``payload`` is a free-form dict that MUST include a
          ``"status"`` key and may include ``"event"``, ``"delta"``, or
          other provider-specific keys (Property 38, Requirements 13.7,
          15.7);
        * return a
          :class:`~ai_toolkit.services.models.responses.GenerationResponse`
          on success, or one of its task-specific siblings (e.g.
          :class:`~ai_toolkit.services.models.responses.ChatCompletionResponse`)
          when the registered task expects a richer response;
        * raise an exception on failure. The executor catches the
          exception, transitions the job to ``failed``, and assigns
          ``str(exc)`` to
          :attr:`~ai_toolkit.services.jobs.job.Generation_Job.failure_reason`
          (Requirements 3.7, 4.10, 5.10).

        Args:
            task: One of the Task Identifiers from the requirements
                glossary. Allows providers that support multiple tasks
                to dispatch internally. Always present in
                :meth:`get_supported_tasks`.
            request: One of the request dataclasses in
                :mod:`ai_toolkit.services.models.requests`. Typed
                :class:`~typing.Any` here so this ABC does not need to
                enumerate every request type, but providers should
                document which concrete request types they accept.
            credentials: Plain dict mapping
                :attr:`CredentialField.key` to user-entered values, as
                pushed in by the UI layer at submission time.
            on_status: Status callback ``(job_id, payload)``. Safe to
                call from any thread; the executor wraps it so that
                callback exceptions never propagate back into the
                provider (Requirements 13.8, 15.9).
            cancel_event: Cooperative cancellation channel. Polling
                cadence is provider-specific; long network calls
                should be bounded so a cancel arrives within a few
                seconds (Requirement 4.7).
        """
