"""Launcher icon loader with a three-tier fallback chain.

Order:
    1. user-provided custom path (if non-empty)
    2. built-in ``launcher_default.png`` shipped with the addon
    3. a procedurally-generated fallback graphic

A non-null icon is ALWAYS returned. Each failure step logs a warning that
mentions the path or stage that failed, in keeping with Property 3 and
Requirements 1.6, 1.13, 1.14, 12.3, 12.4, 12.5, 12.6, 12.11.

``bpy`` is imported lazily inside :meth:`LauncherIcon.load` so this module
remains importable in tooling and tests that run outside Blender.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("ai_toolkit")

ICON_DIR = os.path.dirname(__file__)
BUILTIN_ICON_PATH = os.path.join(ICON_DIR, "launcher_default.png")

# Name used for the procedural fallback Blender Image data-block. Kept stable
# so repeated calls reuse the same image rather than creating a new one
# every draw cycle.
_FALLBACK_IMAGE_NAME = "ai_toolkit_launcher_fallback"

# Accent colour for the procedural fallback. Matches ``launcher_default.png``
# (#4f8cff) so the launcher button reads consistently regardless of which
# tier produced the icon.
_FALLBACK_RGBA = (0x4F / 255.0, 0x8C / 255.0, 0xFF / 255.0, 1.0)
_FALLBACK_SIZE = 32


class LauncherIcon:
    """A Blender Image data-block plus the fallback tier that produced it.

    Attributes:
        image: the underlying ``bpy.types.Image``. Typed ``Any`` so this
            module can be imported without ``bpy`` available.
        source: which tier of the fallback chain produced ``image``. One
            of ``"custom"``, ``"builtin"``, or ``"fallback"``. Useful for
            future debug overlays; not required by tests.
    """

    def __init__(self, image: Any, source: str) -> None:
        self.image = image
        self.source = source

    @classmethod
    def load(cls, custom_path: Optional[str] = None) -> "LauncherIcon":
        """Return a :class:`LauncherIcon`, descending the fallback chain.

        Always returns a non-null :class:`LauncherIcon`. Every failure
        path logs a warning that includes the offending path (or the
        stage name for the built-in tier).
        """
        # Lazy import: keeps this module importable outside Blender.
        import bpy  # type: ignore[import-not-found]

        # Tier 1: user-provided custom path.
        if custom_path:
            if not os.path.isfile(custom_path):
                logger.warning(
                    "Launcher custom icon path does not exist: %s; "
                    "falling back to built-in icon.",
                    custom_path,
                )
            else:
                try:
                    img = bpy.data.images.load(custom_path, check_existing=True)
                    return cls(img, source="custom")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to load launcher custom icon at %s: %s; "
                        "falling back to built-in icon.",
                        custom_path,
                        exc.__class__.__name__,
                    )

        # Tier 2: built-in icon shipped with the addon.
        if os.path.isfile(BUILTIN_ICON_PATH):
            try:
                img = bpy.data.images.load(BUILTIN_ICON_PATH, check_existing=True)
                return cls(img, source="builtin")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to load built-in launcher icon at %s: %s; "
                    "falling back to procedural graphic.",
                    BUILTIN_ICON_PATH,
                    exc.__class__.__name__,
                )
        else:
            logger.warning(
                "Built-in launcher icon missing at %s; "
                "falling back to procedural graphic.",
                BUILTIN_ICON_PATH,
            )

        # Tier 3: procedurally generated fallback. Always succeeds so the
        # launcher button is never invisible.
        existing = bpy.data.images.get(_FALLBACK_IMAGE_NAME)
        if existing is not None:
            return cls(existing, source="fallback")

        img = bpy.data.images.new(
            _FALLBACK_IMAGE_NAME,
            width=_FALLBACK_SIZE,
            height=_FALLBACK_SIZE,
            alpha=True,
        )
        # Flat list of floats, RGBA per pixel, row-major.
        pixels = list(_FALLBACK_RGBA) * (_FALLBACK_SIZE * _FALLBACK_SIZE)
        img.pixels.foreach_set(pixels)
        img.update()
        return cls(img, source="fallback")


__all__ = ["LauncherIcon", "BUILTIN_ICON_PATH"]
