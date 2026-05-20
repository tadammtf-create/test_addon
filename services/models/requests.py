"""The plain-data Request dataclasses exchanged across the UI -> service boundary.

Frozen so request objects are safe to pass to worker threads and reuse for
retry/history. Every field annotation resolves to ``str``, ``int``, ``float``,
``bool``, ``bytes``, ``None``, a list/tuple of those, a dict of those, or
another request dataclass composed of the same allowed types -- never a
``bpy`` type, in keeping with the UI / Service layer split (Requirements 13.1
and 13.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


__all__ = [
    "TextTo3DRequest",
    "ImageTo3DRequest",
    "TextureGenerationRequest",
    "TextToImageRequest",
    "RenderPreviewRequest",
    "ChatMessage",
    "ChatCompletionRequest",
    "SceneAnalysisRequest",
]


@dataclass(frozen=True)
class TextTo3DRequest:
    """Request payload for the ``text_to_3d`` task (Req 4.1, 4.2).

    The prompt is the user-entered text; ``seed`` and ``negative_prompt`` are
    optional provider hints that not every backend will honour.
    """

    prompt: str
    seed: Optional[int] = None
    negative_prompt: Optional[str] = None


@dataclass(frozen=True)
class ImageTo3DRequest:
    """Request payload for the ``image_to_3d`` task (Req 5.1, 5.2).

    ``image_path`` is an absolute filesystem path to a .png/.jpg/.jpeg/.webp
    file that has already been validated UI-side (existence, size, decode).
    """

    image_path: str
    seed: Optional[int] = None


@dataclass(frozen=True)
class TextureGenerationRequest:
    """Request payload for the ``texture_generation`` task (Req 6.1, 6.2, 6.4, 13.2).

    ``texture_maps`` is the user's selected subset of
    ``{"base_color", "normal", "roughness", "metallic"}`` and must be
    non-empty. ``object_name`` is the *name* of the selected mesh object --
    never a ``bpy.types.Object`` reference, so this dataclass remains safe to
    cross the UI / Service boundary.
    """

    prompt: str
    texture_maps: tuple[str, ...]
    object_name: str
    seed: Optional[int] = None


@dataclass(frozen=True)
class TextToImageRequest:
    """Request payload for the ``text_to_image`` task (Req 4.1).

    Used by the FluxV1 and FluxV2 providers. ``output_path``, when set,
    instructs the provider where to write the generated image; otherwise the
    provider chooses a temp path and returns it in the response.
    """

    prompt: str
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    output_path: Optional[str] = None


@dataclass(frozen=True)
class RenderPreviewRequest:
    """Request payload for the ``render_preview`` task (Req 7.1, 7.2, 7.3, 7.4).

    ``viewport_screenshot_path`` is an absolute path to the captured
    screenshot. ``scene_description`` is a serialised ``SceneDescription``
    (a plain ``dict`` at this layer; the analyzer's dataclass is converted to
    a dict before being placed here so the request remains primitives-only).
    ``style_preset`` must be one of ``cinematic``, ``photoreal``,
    ``stylised``, ``studio_lighting`` (Req 7.8).
    """

    viewport_screenshot_path: str
    scene_description: dict
    prompt: str = ""
    style_preset: str = "photoreal"
    suggestion_mode: bool = False


@dataclass(frozen=True)
class ChatMessage:
    """A single message in an AI Assistant conversation (Req 8.1, 8.6).

    ``role`` is one of ``"user"``, ``"assistant"``, ``"system"``.
    ``timestamp_iso8601`` is an ISO-8601 timestamp string assigned when the
    message was first created.
    """

    role: str
    content: str
    timestamp_iso8601: str


@dataclass(frozen=True)
class ChatCompletionRequest:
    """Request payload for the ``chat_completion`` task (Req 8.6, 8.7, 8.8).

    ``messages`` is the last 20 entries of the active conversation, sliced
    UI-side. ``scene_context`` is an optional serialised scene description
    used when the user has enabled the "Include scene context" toggle.
    ``stream`` requests token-by-token streaming when the provider supports
    it; providers that do not stream simply ignore the flag.
    """

    messages: tuple[ChatMessage, ...]
    scene_context: Optional[dict] = None
    stream: bool = True


@dataclass(frozen=True)
class SceneAnalysisRequest:
    """Request payload for the ``scene_analysis`` task (Req 9.4).

    ``scene_description`` is a serialised ``SceneDescription`` produced by
    ``SceneAnalyzer.describe(...)``; it is a plain ``dict`` at this layer so
    the request stays bpy-free and JSON-serialisable.
    """

    scene_description: dict
