"""bpy.app.handlers callbacks that persist the AI Assistant conversation.

The conversation lives in scene.ai_toolkit_assistant.messages while
the .blend is open (bpy.types.PropertyGroup CollectionProperty, see
ui/panels/assistant_panel.py task 20.1). Blender DOES persist
PropertyGroup state across save/load by default -- but only when every
field is a simple Blender-native property. We use a pair of handlers
to additionally write a serialised JSON form of the conversation to
a scene custom property. That redundant payload is the canonical
form for archiving via HistoryManager.archive_conversation, and is
also the safety net for the cases where a Blender update changes the
PropertyGroup layout: load_post can read back the JSON even if the
collection is empty.

Per Req 8.12 (the conversation persists with the .blend file) and
Req 8.13 (loaded back on .blend open).
"""

from __future__ import annotations

import logging
from typing import List

import bpy

from ..services.chat import serialize, deserialize
from ..services.models.requests import ChatMessage

logger = logging.getLogger("ai_toolkit")


# Scene custom-property key under which the serialised conversation
# is stored. Using a namespaced key avoids collisions with
# user-defined custom properties.
CUSTOM_PROP_KEY = "ai_toolkit_assistant_serialised_conversation"


def _collect_messages(scene: "bpy.types.Scene") -> List[ChatMessage]:
    """Read scene.ai_toolkit_assistant.messages into a list of ChatMessage."""
    props = getattr(scene, "ai_toolkit_assistant", None)
    if props is None:
        return []
    result: List[ChatMessage] = []
    for entry in props.messages:
        result.append(ChatMessage(
            role=entry.role,
            content=entry.content,
            timestamp_iso8601=entry.timestamp_iso8601,
        ))
    return result


def _restore_messages(scene: "bpy.types.Scene", messages: List[ChatMessage]) -> None:
    """Repopulate scene.ai_toolkit_assistant.messages from a list of ChatMessage."""
    props = getattr(scene, "ai_toolkit_assistant", None)
    if props is None:
        return
    props.messages.clear()
    for msg in messages:
        item = props.messages.add()
        item.role = msg.role
        item.content = msg.content
        item.timestamp_iso8601 = msg.timestamp_iso8601


def _save_pre_handler(_dummy):
    """Serialise every scene's conversation into a custom property.

    bpy.app.handlers signature: callback(dummy). We iterate
    bpy.data.scenes so multi-scene .blend files preserve every
    scene's conversation independently.
    """
    try:
        for scene in bpy.data.scenes:
            try:
                messages = _collect_messages(scene)
                # Always write -- even an empty list -- so a load_post
                # round-trip clears any stale value from a previous save.
                scene[CUSTOM_PROP_KEY] = serialize(messages)
            except Exception:
                logger.exception(
                    "save_pre conversation handler: scene %r serialise failed",
                    getattr(scene, "name", "?"),
                )
    except Exception:
        # Top-level guard so a Blender API regression cannot block save.
        logger.exception("save_pre conversation handler raised")


def _load_post_handler(_dummy):
    """Deserialise every scene's conversation from its custom property."""
    try:
        for scene in bpy.data.scenes:
            try:
                raw = scene.get(CUSTOM_PROP_KEY) if hasattr(scene, "get") else None
                if not raw or not isinstance(raw, str):
                    continue
                messages = deserialize(raw)
                _restore_messages(scene, messages)
            except Exception:
                logger.exception(
                    "load_post conversation handler: scene %r restore failed",
                    getattr(scene, "name", "?"),
                )
    except Exception:
        logger.exception("load_post conversation handler raised")


# Module-level registration markers so register/unregister are idempotent.
_save_pre_registered = False
_load_post_registered = False


def register_handlers() -> None:
    """Add the save_pre and load_post handlers. Idempotent."""
    global _save_pre_registered, _load_post_registered

    if not _save_pre_registered:
        try:
            bpy.app.handlers.save_pre.append(_save_pre_handler)
            _save_pre_registered = True
        except Exception:
            logger.warning(
                "Could not register save_pre handler", exc_info=True,
            )

    if not _load_post_registered:
        try:
            bpy.app.handlers.load_post.append(_load_post_handler)
            _load_post_registered = True
        except Exception:
            logger.warning(
                "Could not register load_post handler", exc_info=True,
            )


def unregister_handlers() -> None:
    """Remove the save_pre and load_post handlers. Idempotent."""
    global _save_pre_registered, _load_post_registered

    if _save_pre_registered:
        try:
            if _save_pre_handler in bpy.app.handlers.save_pre:
                bpy.app.handlers.save_pre.remove(_save_pre_handler)
        except Exception:
            logger.warning(
                "Could not unregister save_pre handler", exc_info=True,
            )
        _save_pre_registered = False

    if _load_post_registered:
        try:
            if _load_post_handler in bpy.app.handlers.load_post:
                bpy.app.handlers.load_post.remove(_load_post_handler)
        except Exception:
            logger.warning(
                "Could not unregister load_post handler", exc_info=True,
            )
        _load_post_registered = False


__all__ = [
    "register_handlers",
    "unregister_handlers",
    "CUSTOM_PROP_KEY",
]
