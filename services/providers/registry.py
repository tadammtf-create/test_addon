"""Discovery and routing for AI provider plug-ins.

This module implements :class:`ProviderRegistry`, the bpy-free service-layer
component that auto-discovers :class:`~ai_toolkit.services.providers.base.AIProvider`
subclasses dropped into ``services/providers/`` at addon registration time and
routes :class:`~ai_toolkit.services.jobs.job.Generation_Job` submissions to the
chosen provider by Task Identifier (Requirements 3.1, 3.2, 3.3, 3.10).

Registry contract
-----------------
:class:`ProviderRegistry` keeps an ordered list of registered provider
instances and an ``id -> instance`` index keyed by ``provider.get_id()``.
Two public registration paths share the same validation:

* :meth:`ProviderRegistry.register` -- programmatic registration. Used
  internally by :meth:`ProviderRegistry.discover` and available for tests
  or hand-wired providers.
* :meth:`ProviderRegistry.discover` -- package walk via
  :func:`pkgutil.iter_modules`. Imports each submodule of the given
  providers package, scans it for :class:`AIProvider` subclasses,
  validates each, and registers it.

A provider's ``get_id()`` MUST be unique within the registry: a duplicate
raises :class:`ValueError` from :meth:`register` and is logged-and-skipped
inside :meth:`discover`.

Routing rules
-------------
:meth:`ProviderRegistry.get_for_task` follows these rules in order
(Requirements 3.4, 3.5):

1. Look up ``settings.default_for(task)``. If it returns the id of a
   currently-registered provider AND that provider's
   ``get_supported_tasks()`` contains ``task``, return that provider.
2. Otherwise return the first provider in
   :meth:`ProviderRegistry.list_for_task` if non-empty.
3. Otherwise return ``None`` so the calling generation module can show
   a "no provider available" message (Requirement 3.6).

:meth:`ProviderRegistry.list_for_task` always preserves registration
order so the UI's per-task dropdown rendering is stable across reloads
(Requirement 3.10).

Per-module isolation guarantee
------------------------------
The discovery walk processes each submodule of the providers package in
isolation: any of the following per-module failures are logged via the
``ai_toolkit`` logger and the rest of the discovery loop continues
(Requirement 3.11):

* the module raises during import (for example
  :class:`ModuleNotFoundError`, :class:`ImportError`);
* the module exposes an :class:`AIProvider` subclass that cannot be
  instantiated because abstract methods are missing
  (:class:`TypeError`);
* the instance fails the ``_validate_concrete_provider`` check (a
  required method is missing or not callable on the instance);
* the instance raises any other exception during construction;
* the instance has the same ``get_id()`` as one already registered.

A module that contains no :class:`AIProvider` subclass is silently
skipped: this is the normal case for ``base.py`` (which defines the
abstract class itself) and ``registry.py`` (this module). Both are
skipped by name as well, so the abstract :class:`AIProvider` and
:class:`ProviderRegistry` are never picked up as candidates regardless
of how the package is configured.

This module is bpy-free per Requirement 13.1.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from types import ModuleType
from typing import Optional

from .base import AIProvider
from ..settings.store import SettingsStore


__all__ = ["ProviderRegistry"]


# Single addon-wide logger; matches the configuration in
# ``services/__init__.py``. UI-layer code uses the same name, so every log
# record from the addon shares one namespace.
_logger = logging.getLogger("ai_toolkit")


# Submodules of the providers package that must NOT be treated as candidate
# provider modules: ``base`` defines the :class:`AIProvider` abstract class
# itself, and ``registry`` is this module. Both are skipped by leaf name so
# the abstract class and the registry are never picked up as candidates.
_SKIPPED_MODULE_LEAVES: frozenset[str] = frozenset({"base", "registry"})


# Methods that every concrete :class:`AIProvider` MUST implement and that
# MUST resolve to a callable on the instance (Requirements 3.1, 3.11). Kept
# in registration-order at the module level so the same tuple drives both
# the standalone validation helper and any future debug printers.
_REQUIRED_METHODS: tuple[str, ...] = (
    "get_id",
    "get_display_name",
    "get_supported_tasks",
    "get_required_credentials",
    "validate_credentials",
    "submit_job",
)


def _validate_concrete_provider(p: AIProvider) -> None:
    """Validate that ``p`` is a fully-fleshed :class:`AIProvider` instance.

    Asserts every method in :data:`_REQUIRED_METHODS` is present on ``p``
    AND resolves to a callable on the instance. Raises :class:`TypeError`
    naming the missing or non-callable method so :meth:`ProviderRegistry.discover`
    can log a clear error and continue past the offending module
    (Requirement 3.11).

    Also asserts ``isinstance(p, AIProvider)`` so a stray duck-typed object
    that happens to expose all six methods cannot slip into the registry.
    """
    if not isinstance(p, AIProvider):
        raise TypeError(
            f"object of type {type(p).__name__!s} is not an AIProvider instance"
        )
    for method_name in _REQUIRED_METHODS:
        if not hasattr(p, method_name):
            raise TypeError(
                f"AIProvider {type(p).__name__!s} is missing required method "
                f"{method_name!r}"
            )
        if not callable(getattr(p, method_name)):
            raise TypeError(
                f"AIProvider {type(p).__name__!s} attribute {method_name!r} "
                f"is not callable"
            )


class ProviderRegistry:
    """Ordered registry of :class:`AIProvider` instances with task routing.

    The registry is the single source of truth for which providers are
    available to a Generation_Module. It owns:

    * an ordered list of registered providers (registration order, used
      by :meth:`list_for_task`); and
    * an ``id -> instance`` index used by :meth:`get_for_task` to honour
      the user's per-task default selection from
      :meth:`SettingsStore.default_for`.

    Both registration paths -- the programmatic :meth:`register` and the
    package-walking :meth:`discover` -- share the same validation through
    :func:`_validate_concrete_provider`, so a provider that satisfies the
    contract via one path is guaranteed to satisfy it via the other
    (Requirements 3.1, 3.11).
    """

    def __init__(self, settings: SettingsStore) -> None:
        """Bind the registry to ``settings`` for default-provider lookup.

        ``settings`` is consulted only by :meth:`get_for_task`, which calls
        ``settings.default_for(task)`` to obtain the user's per-task
        default-provider id. The registry never writes to ``settings``;
        the AddonPreferences shell owns the write path.
        """
        self._settings: SettingsStore = settings
        # Registration-order list. Drives :meth:`list_for_task` and
        # :meth:`all_providers` (Requirement 3.10).
        self._providers: list[AIProvider] = []
        # ``get_id() -> instance`` index. Populated by :meth:`register` so
        # :meth:`get_for_task` can resolve a settings selection in O(1).
        self._by_id: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider) -> None:
        """Register ``provider`` under its ``get_id()``.

        Validates the provider via :func:`_validate_concrete_provider`
        (every required method present and callable on the instance) and
        raises :class:`ValueError` if a provider with the same id is
        already registered (Requirement 3.10 implies unique ids: the
        registry is keyed by id).

        :meth:`discover` calls this method internally so the two
        registration paths share validation. A test or a hand-wired addon
        can call this method directly when the providers package walk is
        not desired.
        """
        _validate_concrete_provider(provider)
        provider_id = provider.get_id()
        if provider_id in self._by_id:
            raise ValueError(
                f"a provider with id {provider_id!r} is already registered"
            )
        self._providers.append(provider)
        self._by_id[provider_id] = provider

    def discover(self, providers_pkg: str | ModuleType) -> int:
        """Walk ``providers_pkg`` and register every valid provider it finds.

        ``providers_pkg`` may be either a string package name (it will be
        imported with :func:`importlib.import_module`) or an
        already-imported package :class:`~types.ModuleType` (used by tests
        that build a synthetic providers package on a temporary path).

        Returns the count of providers successfully registered during this
        call. Per-module failures are caught, logged via the ``ai_toolkit``
        logger, and skipped so the remaining submodules still get a chance
        (Requirement 3.11). Failure modes covered:

        * :class:`ModuleNotFoundError` / :class:`ImportError` raised by the
          submodule itself;
        * the submodule has no :class:`AIProvider` subclass (silent skip;
          not logged as an error -- having no provider is valid for, e.g.,
          ``base.py`` and ``registry.py`` themselves, which are also
          skipped by name);
        * an :class:`AIProvider` subclass cannot be instantiated because
          abstract methods are missing (:class:`TypeError`);
        * any other exception raised during instantiation;
        * the instance fails :func:`_validate_concrete_provider`;
        * the instance has the same ``get_id()`` as one already registered.

        ``base.py`` and ``registry.py`` are skipped by leaf name so the
        abstract :class:`AIProvider` and this class are never picked up as
        candidates regardless of how the package is laid out.
        """
        # 0. Resolve the package argument to a module object with a
        #    ``__path__``. A failure here logs and returns 0; nothing was
        #    registered so the count is correct.
        if isinstance(providers_pkg, str):
            try:
                pkg = importlib.import_module(providers_pkg)
            except Exception as exc:
                _logger.error(
                    "Provider package %s failed to import: %s",
                    providers_pkg, exc.__class__.__name__,
                )
                return 0
        else:
            pkg = providers_pkg

        if not hasattr(pkg, "__path__"):
            _logger.error(
                "Provider package %s is not a package (no __path__ attribute)",
                getattr(pkg, "__name__", repr(pkg)),
            )
            return 0

        registered_before = len(self._providers)
        prefix = pkg.__name__ + "."

        for _finder, qualname, ispkg in pkgutil.iter_modules(
            pkg.__path__, prefix=prefix,
        ):
            # Subpackages are not walked recursively: the project-internal
            # convention is one provider per top-level module under
            # ``services/providers/``. A subpackage with its own provider
            # tree would still be discoverable by passing it explicitly to
            # a separate :meth:`discover` call.
            if ispkg:
                continue

            leaf = qualname.rsplit(".", 1)[-1]
            if leaf in _SKIPPED_MODULE_LEAVES:
                continue

            # 1. Import the candidate submodule in isolation. Any exception
            #    -- ImportError, ModuleNotFoundError, SyntaxError, a stray
            #    AttributeError raised by a top-level statement -- is
            #    caught so the rest of the package can still load.
            try:
                module = importlib.import_module(qualname)
            except Exception as exc:
                _logger.error(
                    "Provider module %s failed to import: %s",
                    qualname, exc.__class__.__name__,
                )
                continue

            # 2. Scan the module's namespace for :class:`AIProvider`
            #    subclasses. Re-exports (``obj.__module__ != qualname``)
            #    are skipped so a provider class imported from a sibling
            #    module is not double-registered.
            for _name, obj in vars(module).items():
                if not inspect.isclass(obj):
                    continue
                if obj is AIProvider:
                    continue
                if not issubclass(obj, AIProvider):
                    continue
                if getattr(obj, "__module__", None) != qualname:
                    continue

                # 3. Instantiate. Missing abstract methods raise TypeError
                #    from :func:`abc.ABCMeta.__call__`; any other
                #    construction failure is caught the same way so a
                #    provider with a buggy ``__init__`` does not abort
                #    discovery (Requirement 3.11).
                try:
                    instance = obj()
                except TypeError as exc:
                    _logger.error(
                        "Provider class %s in %s cannot be instantiated: %s",
                        obj.__name__, qualname, exc,
                    )
                    continue
                except Exception as exc:
                    _logger.error(
                        "Provider class %s in %s raised during construction: %s",
                        obj.__name__, qualname, exc,
                    )
                    continue

                # 4. Validate the instance. Catches the case where every
                #    abstract method is technically defined on the class
                #    but one of them was shadowed by a non-callable
                #    attribute on the instance after construction.
                try:
                    _validate_concrete_provider(instance)
                except TypeError as exc:
                    _logger.error(
                        "Provider class %s in %s failed validation: %s",
                        obj.__name__, qualname, exc,
                    )
                    continue

                # 5. Register. The duplicate-id check is the only
                #    remaining failure mode; :meth:`register` re-validates
                #    defensively so it remains safe as a public method.
                try:
                    self.register(instance)
                except ValueError as exc:
                    _logger.error(
                        "Provider class %s in %s rejected: %s",
                        obj.__name__, qualname, exc,
                    )
                    continue
                except TypeError as exc:
                    # Reachable only if :meth:`register`'s defensive
                    # re-validation flags something the standalone
                    # :func:`_validate_concrete_provider` missed. Logged
                    # for parity with the other failure paths.
                    _logger.error(
                        "Provider class %s in %s rejected by register(): %s",
                        obj.__name__, qualname, exc,
                    )
                    continue

        return len(self._providers) - registered_before

    def list_for_task(self, task: str) -> list[AIProvider]:
        """Return providers whose ``get_supported_tasks()`` contains ``task``.

        Order is registration order (Requirement 3.10). Returns an empty
        list when no provider supports ``task``; the caller (typically
        :meth:`get_for_task` or a UI dropdown builder) is responsible for
        deciding what to do in that case.
        """
        return [
            p for p in self._providers if task in p.get_supported_tasks()
        ]

    def get_for_task(self, task: str) -> Optional[AIProvider]:
        """Return the provider routed for ``task`` per the rules in Req 3.4.

        Resolution order:

        1. If :meth:`SettingsStore.default_for` returns the id of a
           currently-registered provider AND that provider's
           ``get_supported_tasks()`` contains ``task``, return it. The
           "supports the task" guard is what makes the selector safe in
           the face of a stale settings value left over from a previous
           session whose provider mix differed (Requirement 14.5).
        2. Otherwise return the first entry of :meth:`list_for_task` if
           non-empty.
        3. Otherwise return ``None`` so the calling Generation_Module
           can surface a "no provider available" message and refuse to
           submit (Requirement 3.6).
        """
        selected_id = self._settings.default_for(task)
        if selected_id is not None:
            chosen = self._by_id.get(selected_id)
            if chosen is not None and task in chosen.get_supported_tasks():
                return chosen
        candidates = self.list_for_task(task)
        if candidates:
            return candidates[0]
        return None

    def all_providers(self) -> list[AIProvider]:
        """Return every registered provider in registration order.

        Used by the UI's :class:`AddonPreferences` shell to render one
        credentials section per provider regardless of which tasks the
        provider supports (Requirement 14.2). Returns a fresh list so the
        caller can mutate it without disturbing the registry's internal
        state.
        """
        return list(self._providers)
