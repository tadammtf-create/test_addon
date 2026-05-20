"""bpy-free settings store for the AI Toolkit.

This module implements :class:`SettingsStore`, the service-layer accessor
that the addon uses to read and write user-facing settings without
touching :mod:`bpy`. The store wraps an injectable backing
:class:`~typing.MutableMapping` (a plain :class:`dict` by default) so
that:

* Unit tests can construct an isolated store with no Blender installed
  (Requirements 13.1, 13.4, 13.6).
* The UI layer's :class:`bpy.types.AddonPreferences` shell can later
  inject a dict-like view backed by Blender's persistent preferences
  storage (Requirement 14.7).

Clamping invariant
------------------
:data:`~ai_toolkit.services.settings.schema.LAUNCHER_OFFSET_X` and
:data:`~ai_toolkit.services.settings.schema.LAUNCHER_OFFSET_Y` are clamped
to the inclusive range
``[LAUNCHER_OFFSET_MIN, LAUNCHER_OFFSET_MAX]`` on **both**
:meth:`SettingsStore.set` and :meth:`SettingsStore.get`. Clamping on
write keeps newly-stored values in range; clamping on read defends
against legacy out-of-range values that may have been written by an
older build or directly into the backing storage. This implements
Requirement 1.2.

Default-provider lookup
-----------------------
:meth:`SettingsStore.default_for` consults
``backing[default_provider_key(task_id)]`` only. It deliberately does
**not** fall back to :data:`~ai_toolkit.services.settings.schema.DEFAULTS`
because the per-task default depends on which providers happen to be
registered at runtime. Returning ``None`` when no user choice has been
made signals to ``ProviderRegistry.get_for_task`` that it should fall
back to the first registered provider supporting the task
(Requirements 3.4, 14.5).

Per Requirement 13.1 this module is strictly bpy-free.
"""

from __future__ import annotations

from typing import Any, MutableMapping, Optional

from .schema import (
    DEFAULTS,
    LAUNCHER_OFFSET_MAX,
    LAUNCHER_OFFSET_MIN,
    LAUNCHER_OFFSET_X,
    LAUNCHER_OFFSET_Y,
    default_provider_key,
)


__all__ = ["SettingsStore"]


# Keys whose stored and returned values are clamped to the launcher offset
# range on both read and write. Kept as a module-level frozenset so the
# membership check on every get/set is O(1) and the tuple of clamped keys
# is declared exactly once.
_OFFSET_KEYS: frozenset[str] = frozenset({LAUNCHER_OFFSET_X, LAUNCHER_OFFSET_Y})


def _clamp_offset(n: Any) -> int:
    """Clamp ``n`` into the inclusive launcher offset range.

    Accepts Python :class:`int` and :class:`float` inputs because legacy
    Blender preferences sometimes round-trip integer values as
    :class:`float`. Booleans are accepted because :class:`bool` is a
    subclass of :class:`int` (Python's normal coercion rules).

    Raises :class:`TypeError` for any other type so a caller that
    accidentally hands the store a non-numeric value (a string, a
    :class:`bytes`, ``None``) gets a loud failure rather than a silent
    cast.

    Implements Requirement 1.2 (offset clamp range).
    """
    if not isinstance(n, (int, float)):
        raise TypeError(
            f"launcher offset must be int or float, got {type(n).__name__}"
        )
    coerced = int(n)
    if coerced < LAUNCHER_OFFSET_MIN:
        return LAUNCHER_OFFSET_MIN
    if coerced > LAUNCHER_OFFSET_MAX:
        return LAUNCHER_OFFSET_MAX
    return coerced


class SettingsStore:
    """bpy-free key/value accessor for AI Toolkit settings.

    The store is a thin wrapper over a
    :class:`~typing.MutableMapping` (a plain :class:`dict` by default).
    It owns no I/O of its own; persistence is the responsibility of the
    UI-layer ``AddonPreferences`` shell that injects a backing
    dict-like view of Blender's preferences storage on register.

    The store enforces two contracts on top of the backing mapping:

    * Launcher-offset clamping on read and write (Requirement 1.2).
    * Default-provider-per-task resolution that returns ``None`` for
      unset keys so ``ProviderRegistry`` can fall back to the first
      registered provider (Requirements 3.4, 14.5).

    Every other key is read and written verbatim, with
    :data:`~ai_toolkit.services.settings.schema.DEFAULTS` consulted on
    read for any key whose value has not been written.
    """

    def __init__(
        self,
        backing: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        """Construct a store backed by ``backing``.

        When ``backing`` is ``None`` a fresh empty :class:`dict` is
        created so each store instance gets its own in-memory namespace
        (the typical configuration for unit tests). The UI layer passes
        in a dict-like adapter over Blender's
        :class:`bpy.types.AddonPreferences` instead, so writes are
        persisted across Blender restarts (Requirement 14.7).
        """
        self._backing: MutableMapping[str, Any] = (
            backing if backing is not None else dict()
        )

    def get(self, key: str) -> Any:
        """Return the stored value for ``key``, or its default.

        Resolution order:

        1. If ``key`` is in the backing mapping, that value is returned
           (after clamping for launcher-offset keys).
        2. Otherwise, the default from
           :data:`~ai_toolkit.services.settings.schema.DEFAULTS` is
           returned (also clamped for offset keys, although the
           shipped defaults are already in range).
        3. If neither source has a value, ``None`` is returned. This is
           the path used by per-task default-provider lookups, where
           the absence of a stored value is meaningful (see
           :meth:`default_for`).

        Launcher-offset values are clamped to
        ``[LAUNCHER_OFFSET_MIN, LAUNCHER_OFFSET_MAX]`` on read so that a
        legacy out-of-range write surfaces in range to every consumer
        (Requirement 1.2).
        """
        if key in self._backing:
            value = self._backing[key]
        else:
            value = DEFAULTS.get(key, None)
        if key in _OFFSET_KEYS and value is not None:
            return _clamp_offset(value)
        return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` in the backing mapping.

        Launcher-offset values are clamped to
        ``[LAUNCHER_OFFSET_MIN, LAUNCHER_OFFSET_MAX]`` before being
        written so that the persisted value is always in range
        (Requirement 1.2). Non-numeric writes to an offset key raise
        :class:`TypeError` (see :func:`_clamp_offset`).

        All other keys are stored verbatim. The store does no schema
        validation beyond offset clamping; callers are expected to
        supply values consistent with the type implied by
        :data:`~ai_toolkit.services.settings.schema.DEFAULTS` (or, for
        per-task default-provider keys, a registered provider id).
        """
        if key in _OFFSET_KEYS:
            self._backing[key] = _clamp_offset(value)
            return
        self._backing[key] = value

    def default_for(self, task_id: str) -> Optional[str]:
        """Return the user-selected default provider id for ``task_id``.

        Looks up ``backing[default_provider_key(task_id)]`` only:
        :data:`~ai_toolkit.services.settings.schema.DEFAULTS` is
        intentionally **not** consulted because the appropriate
        per-task default depends on which providers are registered at
        runtime. When no value has been written, this method returns
        ``None`` so
        :meth:`ProviderRegistry.get_for_task <ai_toolkit.services.providers.registry.ProviderRegistry.get_for_task>`
        can fall back to the first registered provider that supports
        the task (Requirements 3.4, 14.5).
        """
        return self._backing.get(default_provider_key(task_id), None)

    def __repr__(self) -> str:
        """Return a debug representation showing the backing key count."""
        return f"SettingsStore(backing_keys={len(self._backing)})"
