"""Pure helpers for the AI Texturing generation module.

Currently exposes :func:`toggle_texture_map`, the bpy-free state-transition
function that drives the UI's multi-select. This module is part of the
**Service Layer** and MUST stay bpy-free per Requirement 13.1.

The AI Texturing module's UI offers a multi-select for the four
texture-map kinds (``base_color``, ``normal``, ``roughness``,
``metallic``); ``base_color`` is selected by default and the user MUST
keep at least one map selected at all times (Req 6.2). The pure helper
here drives that UI: given the current set and a toggle action, it
returns the resulting set or signals rejection.

The helper returns a structured :class:`TextureMapToggleResult` rather
than raising on rejection because UI code passes user-controlled strings
(every checkbox click is a toggle) and a graceful rejection path is
friendlier to the modal UI than wrapping every click in ``try/except``.
The same type makes the rejected-vs-applied branches trivial to assert
against in the property-based test (task 18.4 / Property 17 -- "at least
one texture map remains selected through any sequence of toggles").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, FrozenSet, Iterable


__all__ = [
    "TEXTURE_MAP_KINDS",
    "DEFAULT_TEXTURE_MAPS",
    "toggle_texture_map",
    "TextureMapToggleResult",
]


TEXTURE_MAP_KINDS: Final[FrozenSet[str]] = frozenset(
    {"base_color", "normal", "roughness", "metallic"}
)
"""The four texture-map kinds the AI Texturing module supports.

Defined verbatim from Requirement 6.2; a :class:`frozenset` so callers
cannot mutate the module-level constant.
"""


DEFAULT_TEXTURE_MAPS: Final[FrozenSet[str]] = frozenset({"base_color"})
"""The set selected by default on first render of the AI Texturing panel.

Per Requirement 6.2 ``base_color`` is selected by default; the
"at least one map selected at all times" invariant is preserved by
:func:`toggle_texture_map`.
"""


@dataclass(frozen=True)
class TextureMapToggleResult:
    """The result of one toggle attempt.

    A frozen dataclass so callers can keep a reference to the previous
    result without worrying about another caller mutating it underfoot;
    attempting to assign to any field raises
    :class:`dataclasses.FrozenInstanceError`.

    Attributes:
        selected: The resulting set of selected texture-map kinds. On a
            successful toggle this differs from the input by exactly
            one element; on a rejected toggle this is the input,
            unchanged but normalised to a :class:`frozenset`.
        rejected: ``True`` iff the toggle was refused (unknown kind, or
            attempt to deselect the last remaining map). Callers
            inspect this to decide whether to flash an error indicator
            or silently update the UI state.
        reason: Human-readable reason when ``rejected`` is ``True``;
            the empty string on accepted toggles. Suitable for direct
            display in a tooltip or status line.
    """

    selected: FrozenSet[str]
    rejected: bool
    reason: str = ""


def toggle_texture_map(
    selected: Iterable[str],
    kind: str,
) -> TextureMapToggleResult:
    """Apply a toggle on ``kind`` against the current ``selected`` set.

    Returns a :class:`TextureMapToggleResult` whose ``selected`` is the
    resulting set. When the toggle would leave the set empty (only
    possible when the user tries to deselect their last selection) the
    result has ``rejected=True``, ``selected`` unchanged (but
    normalised to a :class:`frozenset`), and ``reason`` set.

    Pure: no I/O, no logging, no global state. Implements Requirement
    6.2; backs Property 17 (the at-least-one-map invariant).

    Args:
        selected: The currently-selected texture-map kinds, in any
            iterable form. Typically a :class:`frozenset` from the
            previous call's result, but a ``set``, ``list``, or
            ``tuple`` is also accepted; duplicate entries are
            deduplicated by the conversion to ``frozenset``.
        kind: The texture-map kind the user clicked. UI code passes
            this through unchanged from the click handler so unknown
            strings are gracefully rejected rather than raising.

    Returns:
        On unknown ``kind``: ``rejected=True``, ``selected`` unchanged
        (normalised to ``frozenset``), ``reason`` mentions the
        offending kind.

        On a toggle that would empty the set: ``rejected=True``,
        ``selected`` unchanged, ``reason`` describes the constraint.

        Otherwise: ``rejected=False``, ``selected`` is the input set
        with ``kind`` added or removed, ``reason`` is the empty string.
    """
    current: FrozenSet[str] = frozenset(selected)

    # Validate the requested kind first; an unknown kind is rejected
    # regardless of whether it happens to already be in ``current``,
    # because the caller is asking us to perform a meaningless
    # transition and silently no-oping would hide a UI bug.
    if kind not in TEXTURE_MAP_KINDS:
        return TextureMapToggleResult(
            selected=current,
            rejected=True,
            reason=f"unknown texture-map kind {kind!r}",
        )

    if kind in current:
        # Deselect path. Refuse if this would empty the set; the
        # at-least-one-map invariant in Req 6.2 is the whole point of
        # this helper.
        if len(current) == 1:
            return TextureMapToggleResult(
                selected=current,
                rejected=True,
                reason="at least one texture map must remain selected",
            )
        return TextureMapToggleResult(
            selected=current - {kind},
            rejected=False,
        )

    # Select path: ``kind`` is valid and not currently selected.
    return TextureMapToggleResult(
        selected=current | {kind},
        rejected=False,
    )
