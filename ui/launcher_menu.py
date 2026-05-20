"""AITK_OT_launcher_menu modal operator.

The Launcher Menu is the always-on entry point to the addon's five
generation modules plus Documentation and Settings (Requirements 17.1,
17.2). It is opened by:

* the floating launcher's click router (task 12.2);
* the fallback sidebar entry (task 23.3);
* a future global keyboard shortcut (out of scope for this task).

It is rendered via a temporary
:func:`bpy.types.SpaceView3D.draw_handler_add` registration that lives
for the lifetime of the running modal so the menu "floats" above the 3D
Viewport without needing a separate window. ``modal()`` drives hover
highlighting, click dispatch, and cancellation per Requirements 2.3,
2.4, 2.5, 2.6, 2.7, 2.8, 2.10.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9,
2.10, 17.1, 17.2, 17.4.
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar, List, Optional, Tuple

import bpy

from ..services.validation import hit_test


logger = logging.getLogger("ai_toolkit")


# ---------------------------------------------------------------------------
# UI-side module registry.
# ---------------------------------------------------------------------------
#
# Exactly the five module entries required for the initial release
# (Requirement 17.1): Text_To_3D, Image_To_3D, AI_Texturing,
# Render_Preview, AI_Assistant. Each entry is ``(label, bl_idname)``
# where ``bl_idname`` is the operator the menu invokes when the user
# clicks the row. The target panel-show operators are added by tasks
# 16-20; this menu's ``_dispatch`` catches and logs the failure when an
# operator is not yet registered, so the menu degrades gracefully
# during incremental development (Requirement 2.10).
MODULE_REGISTRY: List[Tuple[str, str]] = [
    ("Text-to-3D", "aitk.show_text_to_3d_panel"),
    ("Image-to-3D", "aitk.show_image_to_3d_panel"),
    ("AI Texturing", "aitk.show_texturing_panel"),
    ("Render Preview", "aitk.show_render_preview_panel"),
    ("AI Assistant", "aitk.show_assistant_panel"),
]

# Fixed entries appended after the module list (Requirement 2.1). Their
# dispatch operators are added in task 13.2.
FIXED_ENTRIES: List[Tuple[str, str]] = [
    ("Documentation", "aitk.open_documentation"),
    ("Settings", "aitk.open_preferences"),
]


# ---------------------------------------------------------------------------
# Layout constants.
# ---------------------------------------------------------------------------

# Per-item visual height in pixels (label area).
ITEM_HEIGHT = 28
# Inner padding between an item background and its label text.
ITEM_PADDING = 4
# Total menu width in pixels.
MENU_WIDTH = 200
# Border thickness around the menu, included in the total height.
MENU_BORDER = 1
# Height of the inline error banner that appears above the menu when a
# per-item dispatch fails (Requirement 2.10).
ERROR_BANNER_HEIGHT = 22


# ---------------------------------------------------------------------------
# Hard-coded fallback theme colours (RGBA in [0, 1]).
# ---------------------------------------------------------------------------
#
# Full theme integration (reading the user-selected theme via
# ``load_theme(...)``) is wired in tasks 23.1 / 24.1. For task 13.1 we
# use the dark-theme fallback values from ``ui/theme.json`` directly so
# the menu always renders, even when the theme infrastructure has not
# been bound yet (Requirement 12.9).
_BG_PRIMARY_RGBA = (0.122, 0.122, 0.137, 0.95)     # #1f1f23 + alpha
_BG_HOVER_RGBA = (0.165, 0.165, 0.188, 1.0)        # #2a2a30
_ACCENT_RGBA = (0.31, 0.549, 1.0, 1.0)             # #4f8cff
_TEXT_PRIMARY_RGBA = (0.945, 0.945, 0.961, 1.0)    # #f1f1f5
_TEXT_MUTED_RGBA = (0.627, 0.627, 0.659, 1.0)      # #a0a0a8
_ERROR_BG_RGBA = (0.45, 0.10, 0.10, 1.0)           # red-ish banner
_ERROR_TEXT_RGBA = (1.0, 0.95, 0.95, 1.0)
_BORDER_RGBA = (0.0, 0.0, 0.0, 0.6)


class AITK_OT_launcher_menu(bpy.types.Operator):
    """Modal operator that renders the floating Launcher Menu.

    Lifecycle:

    1. :meth:`invoke` records the open position, resets hover/error
       state, registers a temporary ``POST_PIXEL`` draw handler on
       :class:`bpy.types.SpaceView3D`, and starts the modal handler.
    2. :meth:`modal` consumes mouse and keyboard events: hover
       tracking, click dispatch, click-outside-to-close, and Escape to
       cancel (Requirements 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.10).
    3. :meth:`cancel` removes the draw handler and forces a redraw so
       the menu disappears immediately.
    """

    bl_idname = "aitk.launcher_menu"
    bl_label = "AI Toolkit Launcher"
    bl_options = {"INTERNAL"}

    # Per-instance state (populated in ``invoke``). Declared at the
    # class level only for type-checker clarity; assignments live on
    # the operator instance, not the class.
    _draw_handle: ClassVar[Optional[object]] = None
    _origin_x: int = 0
    _origin_y: int = 0
    _hover_index: int = -1
    _last_error: str = ""

    @classmethod
    def poll(cls, context):
        """Allow invocation only from a 3D Viewport area.

        Matches Requirement 1.8 (overlay only in ``VIEW_3D``) and the
        modal operator pattern used by ``AITK_OT_job_dispatcher``.
        """
        return context.area is not None and context.area.type == "VIEW_3D"

    # ------------------------------------------------------------------
    # bpy.types.Operator overrides.
    # ------------------------------------------------------------------
    def invoke(self, context, event):
        """Open the menu at the cursor position and start the modal loop.

        Implements Requirement 2.9 (open at the cursor / launcher
        position) and Requirement 17.4 (200 ms open budget — measured
        and logged at debug level so regressions are visible without
        hard-failing the user).
        """
        t0 = time.perf_counter()

        # Capture the open position. ``mouse_region_x/y`` are in
        # bottom-left-origin pixel space, matching ``hit_test`` and the
        # ``POST_PIXEL`` draw handler coordinate system.
        self._origin_x = int(getattr(event, "mouse_region_x", 0))
        self._origin_y = int(getattr(event, "mouse_region_y", 0))

        # Fresh menu: nothing hovered, no prior error showing.
        self._hover_index = -1
        self._last_error = ""

        # Register the draw handler for the lifetime of the modal.
        # ``POST_PIXEL`` puts coordinates in region-pixel space, which
        # lines up with our hit-test rectangles.
        try:
            self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                self._draw_callback, (), "WINDOW", "POST_PIXEL"
            )
        except Exception:
            # Failing to register the draw handler is unrecoverable for
            # this operator; bail out cleanly and report.
            logger.exception("Launcher menu failed to register draw handler")
            return {"CANCELLED"}

        # Hand control to Blender's modal dispatcher.
        context.window_manager.modal_handler_add(self)

        # Force the active area to redraw immediately so the menu
        # appears on the next paint, not on the next mouse-move event.
        if context.area is not None:
            context.area.tag_redraw()

        # Requirement 17.4 is informational: log the open latency so
        # regressions are spotted, but do not enforce the 200 ms bound.
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.debug("Launcher menu opened in %.2f ms", elapsed_ms)

        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        """Drive the menu via mouse / keyboard events.

        Event mapping (Requirements 2.3-2.8, 2.10):

        * ``MOUSEMOVE``: refresh the hover index and tag a redraw.
        * ``LEFTMOUSE`` press inside the menu: dispatch the hovered
          item; on dispatch success the menu closes, on dispatch
          failure the menu stays open with an error banner.
        * ``LEFTMOUSE`` press outside the menu: close.
        * ``ESC`` press: cancel.
        * Any other event inside the menu: consume
          (``RUNNING_MODAL``).
        * Any other event outside the menu: pass through to Blender so
          unrelated input keeps working.
        """
        # ``mouse_region_x/y`` are present on mouse events but not on
        # keyboard events; defensive ``getattr`` keeps the modal robust
        # for ``ESC`` and other key presses.
        mx = int(getattr(event, "mouse_region_x", 0))
        my = int(getattr(event, "mouse_region_y", 0))

        inside_menu = hit_test(self._menu_rect(), (mx, my))

        # 1) Hover tracking.
        if event.type == "MOUSEMOVE":
            new_hover = self._index_at(mx, my)
            if new_hover != self._hover_index:
                self._hover_index = new_hover
                if context.area is not None:
                    context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        # 2) Left click — primary activation channel.
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if inside_menu:
                idx = self._index_at(mx, my)
                if idx >= 0:
                    if self._dispatch(context, idx):
                        # Successful dispatch: close menu (Req 2.3).
                        self.cancel(context)
                        return {"FINISHED"}
                    # Failed dispatch: keep the menu open and show
                    # the inline error banner (Req 2.10).
                    return {"RUNNING_MODAL"}
                # Click landed inside the menu rectangle but not on a
                # row (e.g. on the border padding). Keep the menu open.
                return {"RUNNING_MODAL"}
            # Click outside the menu closes without launching anything
            # (Req 2.5).
            self.cancel(context)
            return {"FINISHED"}

        # 3) Escape cancels (Req 2.4).
        if event.type == "ESC" and event.value == "PRESS":
            self.cancel(context)
            return {"CANCELLED"}

        # 4) Everything else: consume inside the menu, pass through
        #    outside (Req 2.8).
        if inside_menu:
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, context):
        """Tear down the draw handler and request a final redraw.

        Idempotent: a missing draw handle is silently ignored so the
        method is safe to call from the success path of :meth:`modal`
        and from the cancel path Blender invokes when the modal slot
        is reclaimed.
        """
        if self._draw_handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    self._draw_handle, "WINDOW"
                )
            except Exception:
                # Blender may have already torn down the handler (for
                # example during shutdown). Log at debug only.
                logger.debug(
                    "Launcher menu: draw_handler_remove raised; "
                    "handler was likely already removed",
                    exc_info=True,
                )
            self._draw_handle = None

        # Force the area to redraw so the menu pixels disappear
        # immediately rather than lingering until the next event.
        if context is not None and getattr(context, "area", None) is not None:
            context.area.tag_redraw()

    # ------------------------------------------------------------------
    # Layout helpers.
    # ------------------------------------------------------------------
    def _items(self) -> List[Tuple[str, str]]:
        """Return the concatenated module + fixed entries list.

        Order matches the visual layout: the five generation modules
        first (top of the menu), then ``Documentation`` and
        ``Settings`` (Requirement 2.1).
        """
        return list(MODULE_REGISTRY) + list(FIXED_ENTRIES)

    def _menu_rect(self) -> Tuple[int, int, int, int]:
        """Return ``(x, y, w, h)`` of the menu in region pixel space.

        The menu opens **upwards** from the cursor: the menu's
        top-right corner sits at the cursor position, and the menu
        extends downwards by ``total_height`` from there. With
        bottom-left origin, that means the menu's bottom is at
        ``origin_y - total_height``.
        """
        n = len(self._items())
        total_height = (n * ITEM_HEIGHT) + (2 * MENU_BORDER)
        return (
            self._origin_x,
            self._origin_y - total_height,
            MENU_WIDTH,
            total_height,
        )

    def _item_rect(self, index: int) -> Tuple[int, int, int, int]:
        """Return ``(x, y, w, h)`` of the menu item at ``index``.

        Item ``0`` is at the top of the menu; the last item is at the
        bottom. Because the coordinate system is bottom-left-origin,
        this means item ``0`` has the largest ``y`` value.
        """
        menu_x, menu_y, _menu_w, _menu_h = self._menu_rect()
        n = len(self._items())
        item_x = menu_x + MENU_BORDER
        item_y = menu_y + MENU_BORDER + (n - 1 - index) * ITEM_HEIGHT
        item_w = MENU_WIDTH - (2 * MENU_BORDER)
        return (item_x, item_y, item_w, ITEM_HEIGHT)

    def _index_at(self, mouse_x: int, mouse_y: int) -> int:
        """Return the menu-item index under ``(mouse_x, mouse_y)`` or -1."""
        for i in range(len(self._items())):
            if hit_test(self._item_rect(i), (mouse_x, mouse_y)):
                return i
        return -1

    # ------------------------------------------------------------------
    # Dispatch.
    # ------------------------------------------------------------------
    def _dispatch(self, context, index: int) -> bool:
        """Invoke the operator for the item at ``index``.

        Returns ``True`` on success (caller closes the menu) and
        ``False`` on any failure. On failure the exception is logged,
        ``self._last_error`` is set to a user-visible message naming
        the failing module (Requirement 2.10), and the area is tagged
        for redraw so the inline error banner appears immediately.
        """
        items = self._items()
        if index < 0 or index >= len(items):
            return False
        label, op_idname = items[index]

        try:
            # Avoid ``eval`` — split the bl_idname into its category
            # and operator name and resolve via ``getattr``. Equivalent
            # to ``bpy.ops.<category>.<name>('INVOKE_DEFAULT')`` but
            # safer.
            if "." not in op_idname:
                raise ValueError(
                    f"Operator id {op_idname!r} is missing a category prefix"
                )
            category, name = op_idname.split(".", 1)
            op_callable = getattr(getattr(bpy.ops, category), name)
            op_callable("INVOKE_DEFAULT")
        except Exception:
            logger.exception(
                "Launcher menu failed to dispatch %s", op_idname
            )
            self._last_error = f"Could not open {label}"
            if context is not None and getattr(context, "area", None) is not None:
                context.area.tag_redraw()
            return False

        return True

    # ------------------------------------------------------------------
    # Drawing.
    # ------------------------------------------------------------------
    def _draw_callback(self) -> None:
        """Render the menu background, items, hover highlight, and error.

        Wrapped in a top-level try/except so a draw error never crashes
        Blender (Requirements 12.5, 12.9). The actual GPU/blf calls
        are delegated to small helpers; failures inside any helper are
        logged and the rest of the draw continues so a partial menu is
        visible even when one element (e.g. text rendering) fails.
        """
        try:
            # Lazy imports keep this module importable in a plain
            # Python interpreter for tooling and the smoke test.
            import gpu  # type: ignore[import-not-found]
            from gpu_extras.batch import batch_for_shader  # type: ignore[import-not-found]
            import blf  # type: ignore[import-not-found]
        except Exception:
            # If the GPU module surface is unavailable for any reason,
            # there is nothing useful we can draw. Log and bail.
            logger.exception("Launcher menu: GPU/blf modules unavailable")
            return

        try:
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            menu_x, menu_y, menu_w, menu_h = self._menu_rect()

            # 1) Menu background.
            self._draw_quad(
                shader, batch_for_shader,
                menu_x, menu_y, menu_w, menu_h,
                _BG_PRIMARY_RGBA,
            )

            # 2) Border (thin frame around the menu).
            self._draw_quad_border(
                shader, batch_for_shader,
                menu_x, menu_y, menu_w, menu_h,
                _BORDER_RGBA,
            )

            # 3) Items, with a different background for the hover row.
            font_id = 0  # Blender's default font.
            try:
                blf.size(font_id, 13)
            except Exception:
                # Older Blender versions accept an extra dpi argument;
                # tolerate the difference silently.
                logger.debug(
                    "Launcher menu: blf.size signature mismatch", exc_info=True
                )

            for i, (label, _op) in enumerate(self._items()):
                ix, iy, iw, ih = self._item_rect(i)
                if i == self._hover_index:
                    self._draw_quad(
                        shader, batch_for_shader,
                        ix, iy, iw, ih,
                        _BG_HOVER_RGBA,
                    )
                # Label.
                try:
                    blf.color(font_id, *_TEXT_PRIMARY_RGBA)
                    blf.position(
                        font_id,
                        ix + ITEM_PADDING + 4,
                        iy + (ih - 13) / 2,
                        0,
                    )
                    blf.draw(font_id, label)
                except Exception:
                    logger.debug(
                        "Launcher menu: label draw failed for %r",
                        label,
                        exc_info=True,
                    )

            # 4) Inline error banner above the menu (Requirement 2.10).
            if self._last_error:
                banner_y = menu_y + menu_h + 2  # 2 px gap above menu
                self._draw_quad(
                    shader, batch_for_shader,
                    menu_x, banner_y, menu_w, ERROR_BANNER_HEIGHT,
                    _ERROR_BG_RGBA,
                )
                try:
                    blf.color(font_id, *_ERROR_TEXT_RGBA)
                    blf.position(
                        font_id,
                        menu_x + ITEM_PADDING + 4,
                        banner_y + (ERROR_BANNER_HEIGHT - 13) / 2,
                        0,
                    )
                    blf.draw(font_id, self._last_error)
                except Exception:
                    logger.debug(
                        "Launcher menu: error banner draw failed",
                        exc_info=True,
                    )
        except Exception:
            # Blanket guard: a draw error must never propagate into
            # Blender's render loop (Requirements 12.5, 12.9).
            logger.exception("Launcher menu draw callback failed")

    @staticmethod
    def _draw_quad(
        shader,
        batch_for_shader,
        x: int,
        y: int,
        w: int,
        h: int,
        color: Tuple[float, float, float, float],
    ) -> None:
        """Render a filled axis-aligned rectangle in ``UNIFORM_COLOR``."""
        try:
            verts = [
                (x, y),
                (x + w, y),
                (x + w, y + h),
                (x, y + h),
            ]
            indices = [(0, 1, 2), (0, 2, 3)]
            batch = batch_for_shader(
                shader, "TRIS", {"pos": verts}, indices=indices
            )
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
        except Exception:
            logger.debug(
                "Launcher menu: _draw_quad failed at (%d, %d, %d, %d)",
                x, y, w, h,
                exc_info=True,
            )

    @staticmethod
    def _draw_quad_border(
        shader,
        batch_for_shader,
        x: int,
        y: int,
        w: int,
        h: int,
        color: Tuple[float, float, float, float],
    ) -> None:
        """Render a 1-pixel rectangular outline."""
        try:
            verts = [
                (x, y),
                (x + w, y),
                (x + w, y + h),
                (x, y + h),
            ]
            batch = batch_for_shader(shader, "LINE_LOOP", {"pos": verts})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
        except Exception:
            logger.debug(
                "Launcher menu: _draw_quad_border failed at (%d, %d, %d, %d)",
                x, y, w, h,
                exc_info=True,
            )


__all__ = ["AITK_OT_launcher_menu", "MODULE_REGISTRY", "FIXED_ENTRIES"]
