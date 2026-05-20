"""Chat conversation utilities for the AI Assistant module.

This module is part of the **Service Layer** and therefore MUST NOT import
any Blender-distributed module (``bpy``, ``bpy_extras``, ``mathutils``,
``bgl``, ``gpu``, ``bmesh``, ``blf``). Per Requirement 13.1 the entire
service layer is built and tested in a plain Python interpreter; the chat
utilities sit on the request-assembly path the AI Assistant module
exercises every time the user hits Send and on the persistence path that
embeds conversations in the saved Blender file, so keeping this module
``bpy``-free is what allows the property-based tests in tasks 9.3 and 9.4
to run without Blender installed.

Two pieces compose this module:

* :func:`build_chat_request` -- assembles a
  :class:`~ai_toolkit.services.models.requests.ChatCompletionRequest`
  from the live conversation by slicing it to the last
  :data:`CHAT_CONTEXT_WINDOW` entries and attaching an optional scene
  description (Req 8.6, 8.7). Implemented in task 9.1.
* :func:`serialize` / :func:`deserialize` -- round-trippable persistence
  of a conversation for embedding in the saved Blender file (Req 8.12,
  8.13, Property 25). Implemented in task 9.2 (this file). The wire
  format is a versioned JSON envelope; :func:`deserialize` is also
  lenient enough to read the bare-list shape written by
  :meth:`HistoryManager.archive_conversation`, so old archives and
  hand-edited data continue to load cleanly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final, Optional, Sequence

from .models.requests import ChatCompletionRequest, ChatMessage


__all__ = [
    "build_chat_request",
    "CHAT_CONTEXT_WINDOW",
    "serialize",
    "deserialize",
    "WIRE_VERSION",
]


# Addon-wide logger. Only ``deserialize`` writes to it, and only on
# failure paths -- the success paths of both serializers are pure: no
# I/O, no logging, no global state mutated.
_logger = logging.getLogger("ai_toolkit")


CHAT_CONTEXT_WINDOW = 20
"""Maximum number of recent messages forwarded to the chat provider.

Per Requirement 8.6 the AI Assistant includes "up to the 20 most recent
messages of the current conversation as context in each
``chat_completion`` Generation_Job". When the conversation is shorter
than 20 messages, every message is included; the slice
``conversation[-CHAT_CONTEXT_WINDOW:]`` handles both regimes uniformly
(see Property 22).
"""


def build_chat_request(
    conversation: Sequence[ChatMessage],
    scene_context: Optional[dict] = None,
    *,
    stream: bool = True,
) -> ChatCompletionRequest:
    """Build a :class:`ChatCompletionRequest` from a conversation.

    Slices ``conversation`` to the last :data:`CHAT_CONTEXT_WINDOW`
    entries in original order (Req 8.6) and attaches the optional
    ``scene_context`` unchanged (Req 8.7). Whether the scene context is
    included is a UI-side decision driven by the "Include scene context"
    toggle (task 22.2); when the toggle is off the caller passes
    ``None`` here.

    Python's slice is forgiving when ``len(conversation) <
    CHAT_CONTEXT_WINDOW`` -- ``[1, 2, 3][-20:] == [1, 2, 3]`` -- and when
    ``conversation`` is empty -- ``[][-20:] == []`` -- so no special
    casing for short or empty inputs is needed.

    The default ``stream=True`` matches :class:`ChatCompletionRequest`'s
    own dataclass default; it is set explicitly here so call sites can
    opt out (``stream=False``) for providers that do not stream without
    reaching past this convenience builder.

    Args:
        conversation: Every message in the active conversation, in
            chronological order. May be any :class:`Sequence` -- a
            ``list``, ``tuple``, or any other indexable, sliceable
            container. May be empty.
        scene_context: Optional plain-``dict`` rendering of a
            :class:`~ai_toolkit.services.scene.analyzer.SceneDescription`,
            converted to a dict UI-side, attached when the "Include
            scene context" toggle is on (Req 8.7). ``None`` when the
            toggle is off or no description is available.
        stream: When the selected provider supports streaming, request
            token-by-token streaming (Req 8.8); providers that do not
            stream simply ignore the flag. Keyword-only so call sites
            never accidentally pass it positionally.

    Returns:
        A frozen :class:`ChatCompletionRequest` whose ``messages`` field
        is a ``tuple`` of the last :data:`CHAT_CONTEXT_WINDOW` entries
        of ``conversation`` in original order, whose ``scene_context``
        is ``scene_context`` unchanged, and whose ``stream`` field is
        ``stream``.
    """
    window = conversation[-CHAT_CONTEXT_WINDOW:]
    return ChatCompletionRequest(
        messages=tuple(window),
        scene_context=scene_context,
        stream=stream,
    )


WIRE_VERSION: Final[int] = 1
"""On-disk format version of the conversation envelope written by :func:`serialize`.

Bumped only when the envelope shape itself changes in a way old readers
would mis-interpret. The current readers tolerate unknown ``version``
values for forwards-compatibility -- they parse the ``messages`` field
and ignore everything else -- so a bump is only required when
``messages`` itself changes shape.
"""


# Canonical fields of one ``ChatMessage`` entry on the wire. Listed once
# here so :func:`serialize` and :func:`deserialize` cannot drift apart,
# and so the validator below can iterate it directly when checking
# ``deserialize`` input.
_MESSAGE_FIELDS: Final[tuple[str, ...]] = ("role", "content", "timestamp_iso8601")


def serialize(conversation: Sequence[ChatMessage]) -> str:
    """Serialise a conversation to a JSON string for embedding in the saved .blend file.

    The wire format is a JSON object with a ``version`` key (set to
    :data:`WIRE_VERSION`) and a ``messages`` array, each element of
    which is an object with the three canonical fields ``role``,
    ``content``, ``timestamp_iso8601``. The version envelope lets
    future format changes be migrated cleanly without breaking older
    saves; if the envelope ever needs a new top-level key (e.g. a
    ``model`` field for the active provider), older readers will
    simply ignore it.

    Round-trip exact: ``deserialize(serialize(c)) == list(c)`` for
    every valid conversation, where "valid" means a sequence of
    :class:`ChatMessage` instances whose three string fields are
    JSON-encodable text (every Python ``str`` is, including non-ASCII)
    -- Property 25, Requirements 8.12 and 8.13.

    Pure: no I/O, no logging. ``ensure_ascii=False`` keeps non-ASCII
    content (Cyrillic, CJK, emoji) verbatim instead of escaping it as
    ``\\uXXXX`` sequences, so the embedded string survives a
    round-trip through Blender's custom-property storage byte-for-byte
    where the storage layer is UTF-8 clean. The compact ``(",", ":")``
    separators trim every avoidable byte because the result is
    embedded in a scene custom property, not displayed to a human.

    Args:
        conversation: Every message in the conversation to persist, in
            chronological order. May be any :class:`Sequence` -- a
            ``list``, ``tuple``, or any other indexable, iterable
            container. May be empty; an empty conversation produces a
            valid wire string that round-trips to ``[]``.

    Returns:
        A JSON string ready to be written into a Blender custom
        property. Always a non-empty string -- even an empty
        conversation produces a small ``{"version":1,"messages":[]}``
        envelope.
    """
    wire: dict[str, Any] = {
        "version": WIRE_VERSION,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "timestamp_iso8601": message.timestamp_iso8601,
            }
            for message in conversation
        ],
    }
    return json.dumps(wire, ensure_ascii=False, separators=(",", ":"))


def _coerce_messages_payload(parsed: Any) -> Optional[list]:
    """Extract the messages list from a parsed wire payload, or ``None``.

    Accepts two shapes:

    * The current envelope ``{"version": ..., "messages": [...]}`` --
      returned by :func:`serialize`.
    * The legacy bare-list shape ``[entry, entry, ...]`` -- written by
      :meth:`HistoryManager.archive_conversation` (which dumps the
      conversation as a plain JSON list) and, by extension, by anyone
      who hand-edits a saved archive. Accepting this shape keeps old
      conversation archives loadable after the v1 envelope ships.

    Returns:
        The list of message-shaped dicts on success, or ``None`` if the
        payload is neither shape. ``None`` is the caller's signal to
        log a warning and return ``[]``.
    """
    if isinstance(parsed, list):
        # Legacy bare-list shape; treat the list itself as the messages
        # array. Validation of each entry happens in :func:`deserialize`.
        return parsed
    if isinstance(parsed, dict):
        messages = parsed.get("messages")
        if isinstance(messages, list):
            return messages
    return None


def deserialize(s: str) -> list[ChatMessage]:
    """Deserialise a conversation string produced by :func:`serialize`.

    Returns a fresh ``list`` of :class:`ChatMessage` instances. On any
    parse error or shape error logs a single warning to the
    ``ai_toolkit`` logger naming the reason and returns ``[]``, so a
    corrupt embedded conversation never blocks Blender from loading
    the file (Req 8.13).

    Tolerated input shapes:

    * The current envelope ``{"version": ..., "messages": [...]}``.
    * The legacy bare-list shape ``[entry, entry, ...]`` -- written by
      :meth:`HistoryManager.archive_conversation` for archived
      conversations and accepted here for backwards compatibility with
      v0 / hand-edited data.

    Empty / missing inputs that round-trip to ``[]`` cleanly:

    * The empty string ``""`` -- treated as "no embedded
      conversation", returns ``[]`` with a single warning log.
    * The string ``"[]"`` -- the legacy empty conversation; returns
      ``[]`` without warning (it parses cleanly as an empty
      bare-list).
    * The envelope ``{"version":1,"messages":[]}`` -- the current
      empty conversation; returns ``[]`` without warning.

    Args:
        s: The previously-serialised conversation string. Typically
            read from a scene custom property by the UI layer's
            ``load_post`` handler; may also be a hand-edited archive
            file's content.

    Returns:
        A fresh ``list`` of :class:`ChatMessage` instances in the same
        order as the on-disk payload. Empty list on any error or on a
        legitimately-empty conversation.
    """
    if not s:
        # Distinguish from "[]" (which parses cleanly): an empty string
        # is never a valid JSON document, so we log the reason
        # explicitly rather than letting json.loads raise.
        _logger.warning(
            "deserialize(conversation): empty input string; returning []"
        )
        return []

    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as exc:
        _logger.warning(
            "deserialize(conversation): JSON parse error (%s); returning []",
            exc,
        )
        return []

    messages_payload = _coerce_messages_payload(parsed)
    if messages_payload is None:
        # Either the root was a non-dict, non-list (e.g. a string or a
        # number) or a dict missing the ``messages`` key. Both are
        # corrupt-input cases; surface a specific reason in the log so
        # an operator looking at the warning can tell which one
        # happened.
        if isinstance(parsed, dict):
            reason = "missing 'messages' key"
        else:
            reason = (
                f"root is {type(parsed).__name__}, expected dict or list"
            )
        _logger.warning(
            "deserialize(conversation): %s; returning []", reason
        )
        return []

    result: list[ChatMessage] = []
    for index, entry in enumerate(messages_payload):
        if not isinstance(entry, dict):
            _logger.warning(
                "deserialize(conversation): entry index %d is %s, "
                "expected dict; returning []",
                index,
                type(entry).__name__,
            )
            return []
        for field in _MESSAGE_FIELDS:
            if field not in entry:
                _logger.warning(
                    "deserialize(conversation): missing %s on entry "
                    "index %d; returning []",
                    field,
                    index,
                )
                return []
            if not isinstance(entry[field], str):
                _logger.warning(
                    "deserialize(conversation): %s on entry index %d "
                    "is %s, expected str; returning []",
                    field,
                    index,
                    type(entry[field]).__name__,
                )
                return []
        result.append(
            ChatMessage(
                role=entry["role"],
                content=entry["content"],
                timestamp_iso8601=entry["timestamp_iso8601"],
            )
        )
    return result
