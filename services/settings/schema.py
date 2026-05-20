"""Settings schema for the AI Toolkit addon.

This module declares the stable string keys used by the bpy-free
``SettingsStore`` (see ``services/settings/store.py``) along with default
values for every key that has a single canonical default. The keys
defined here form the contract between the UI layer's
``AddonPreferences`` shell and the service layer's settings consumer.

All key strings are stable. They identify entries in persistent settings
storage, so changing a string is a breaking change for users with
existing preferences. New keys may be added freely; existing keys must
keep their values.

Prefix convention
-----------------
Per-task default-provider selectors are stored under one key per task,
formed by concatenating the prefix :data:`DEFAULT_PROVIDER_FOR_TASK`
with the task identifier. Use :func:`default_provider_key` to build the
full key for a given task. Because the per-task default depends on which
providers are registered at runtime, this prefix has no entry in
:data:`DEFAULTS`; ``SettingsStore`` falls back to ``None`` when the key
is missing for a given task, signalling to ``ProviderRegistry`` to use
the first registered provider supporting that task (Requirement 3.4).

Clamping
--------
The launcher offset settings (:data:`LAUNCHER_OFFSET_X`,
:data:`LAUNCHER_OFFSET_Y`) are clamped by ``SettingsStore`` to the
inclusive range ``[LAUNCHER_OFFSET_MIN, LAUNCHER_OFFSET_MAX]`` on both
read and write (Requirement 1.2).

Validates: Requirements 1.2, 1.12, 11.1, 12.7, 14.8.

This module is bpy-free per Requirement 13.1.
"""

from __future__ import annotations

from typing import Any, Final


# ---------------------------------------------------------------------------
# Setting keys
# ---------------------------------------------------------------------------

# Floating launcher horizontal offset in pixels, measured from the bottom-left
# of the active 3D Viewport. Clamped on read and write to
# ``[LAUNCHER_OFFSET_MIN, LAUNCHER_OFFSET_MAX]``. Default: 24. Requirement 1.2.
LAUNCHER_OFFSET_X: Final[str] = "launcher.offset_x"

# Floating launcher vertical offset in pixels, measured from the bottom-left
# of the active 3D Viewport. Clamped identically to ``LAUNCHER_OFFSET_X``.
# Default: 24. Requirement 1.2.
LAUNCHER_OFFSET_Y: Final[str] = "launcher.offset_y"

# Whether the floating launcher overlay is rendered in the 3D Viewport.
# When disabled, the UI layer must expose the fallback sidebar entry point
# instead. Default: True. Requirements 1.9, 1.11, 1.12, 14.8.
LAUNCHER_OVERLAY_ENABLED: Final[str] = "launcher.overlay_enabled"

# Absolute filesystem path to a user-provided custom launcher icon, or the
# empty string when no custom icon is configured. The UI layer falls back
# to the built-in icon on empty string or load failure. Default: "".
# Requirements 1.6, 1.13, 1.14.
LAUNCHER_CUSTOM_ICON: Final[str] = "launcher.custom_icon"

# Theme selection. Valid values are ``"dark"``, ``"light"``, and
# ``"match_blender_theme"``. Default: ``"match_blender_theme"``.
# Requirement 12.7.
THEME_SELECTION: Final[str] = "theme.selection"

# Documentation URL opened by the "Documentation" entries in the launcher
# menu and the addon preferences. Validated via ``is_valid_url`` before
# the browser is launched. Requirement 11.1.
DOCUMENTATION_URL: Final[str] = "documentation.url"

# Prefix for per-task default-provider selectors. Concatenate a task
# identifier (for example ``"text_to_3d"``) to form the full key, or call
# :func:`default_provider_key`. This prefix has no ``DEFAULTS`` entry
# because the default provider depends on the runtime registry.
# Requirements 3.4, 14.5.
DEFAULT_PROVIDER_FOR_TASK: Final[str] = "providers.default_for."

# Toggle for remote-history sync. The remote backend itself is explicitly
# out of scope for the initial release (Requirement 17.5); the toggle is
# persisted so a future release can honour it without a settings
# migration. Default: False. Requirement 10.8.
REMOTE_HISTORY_SYNC_ENABLED: Final[str] = "history.remote_sync_enabled"


# ---------------------------------------------------------------------------
# Clamp ranges
# ---------------------------------------------------------------------------

# Inclusive lower bound for the launcher offset settings. Requirement 1.2.
LAUNCHER_OFFSET_MIN: Final[int] = 0

# Inclusive upper bound for the launcher offset settings. Requirement 1.2.
LAUNCHER_OFFSET_MAX: Final[int] = 4096


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Default value for every concrete setting key. The
# ``DEFAULT_PROVIDER_FOR_TASK`` prefix is intentionally absent: it is a
# prefix, not a key, and the per-task default depends on the runtime
# provider registry. ``SettingsStore`` returns ``None`` when a per-task
# key has no stored value.
DEFAULTS: Final[dict[str, Any]] = {
    LAUNCHER_OFFSET_X: 24,
    LAUNCHER_OFFSET_Y: 24,
    LAUNCHER_OVERLAY_ENABLED: True,
    LAUNCHER_CUSTOM_ICON: "",
    THEME_SELECTION: "match_blender_theme",
    DOCUMENTATION_URL: "https://github.com/ai-toolkit/ai-toolkit-platform",
    REMOTE_HISTORY_SYNC_ENABLED: False,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def default_provider_key(task_id: str) -> str:
    """Return the full settings key for the default provider of ``task_id``.

    The returned string is ``DEFAULT_PROVIDER_FOR_TASK + task_id``. When
    no value has been written under this key, ``SettingsStore`` returns
    ``None`` so that ``ProviderRegistry`` can fall back to the first
    registered provider supporting the task (Requirement 3.4).
    """
    return DEFAULT_PROVIDER_FOR_TASK + task_id


__all__ = [
    "LAUNCHER_OFFSET_X",
    "LAUNCHER_OFFSET_Y",
    "LAUNCHER_OVERLAY_ENABLED",
    "LAUNCHER_CUSTOM_ICON",
    "THEME_SELECTION",
    "DOCUMENTATION_URL",
    "DEFAULT_PROVIDER_FOR_TASK",
    "REMOTE_HISTORY_SYNC_ENABLED",
    "LAUNCHER_OFFSET_MIN",
    "LAUNCHER_OFFSET_MAX",
    "DEFAULTS",
    "default_provider_key",
]
