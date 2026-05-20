"""The plain-data Response dataclasses returned from the service layer to the UI layer.

Frozen so they can be safely posted from worker threads and consumed on the main thread
without locking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "GenerationResponse",
    "ChatCompletionResponse",
    "SceneAnalysisResponse",
]


@dataclass(frozen=True)
class GenerationResponse:
    """Response returned by text-to-image, text-to-3D, image-to-3D, texture, and
    render-preview Generation_Jobs.

    Attributes:
        output_file_paths: One or more absolute filesystem paths the provider has
            already written to disk. Order is provider-defined. Stored as a tuple
            so the response is safe to share between threads without copying.
        metadata: Opaque provider-specific metadata (seed, model id, token counts,
            etc.). Consumers should treat unknown keys as informational only.
        provider_message: Human-readable status message. Rendered in UI banners on
            failure, or as informational text on success.
    """

    output_file_paths: tuple[str, ...]
    metadata: dict = field(default_factory=dict)
    provider_message: str = ""


@dataclass(frozen=True)
class ChatCompletionResponse:
    """Response returned by a `chat_completion` Generation_Job.

    Streaming providers emit incremental tokens through the status callback during
    the job. The terminal response carries the fully assembled assistant message.

    Attributes:
        assistant_message: Final concatenated assistant response.
        metadata: Opaque provider-specific metadata (token counts, model id, etc.).
    """

    assistant_message: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SceneAnalysisResponse:
    """Response returned by a `scene_analysis` Generation_Job.

    The UI groups suggestions into the canonical buckets (lighting, topology,
    composition, materials, other) using `category_hints` plus a grouping helper;
    suggestions absent from `category_hints` are placed in the `other` bucket.

    Attributes:
        suggestions: Raw textual suggestions returned by the provider, in the
            order produced.
        category_hints: Optional mapping from suggestion string to one of
            {"lighting", "topology", "composition", "materials", "other"}.
        metadata: Opaque provider-specific metadata.
    """

    suggestions: tuple[str, ...]
    category_hints: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
