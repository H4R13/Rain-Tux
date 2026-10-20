"""Stacking order of skins that share a layer-shell layer.

Rainmeter stacks skins by AlwaysOnTop, then LoadOrder (a higher LoadOrder is on top). Wayland's
layer shell has no request to restack surfaces within a layer: their order is the order the
compositor received them in. Most compositors put a new surface on top of its layer; Sway (1.9,
measured) puts it at the bottom. A window that is unmapped and mapped again is a new surface, so
the engine reorders a layer by re-mapping the fewest windows, in the order :func:`restack_plan`
returns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")


def restack_plan(current: Sequence[T], rank: Callable[[T], Any], new_on_top: bool) -> list[T]:
    """Windows to map again, in this order, so that ``current`` (bottom first) ends up sorted by
    ``rank`` (equal ranks keep their order). A re-mapped window goes on top of the layer when
    ``new_on_top``, else to its bottom."""
    desired = sorted(current, key=rank)

    def in_order(part: list[T]) -> bool:
        return [w for w in current if any(w is p for p in part)] == part

    n = len(desired)
    if new_on_top:  # keep the longest bottom part that is already in order; raise the rest in order
        m = next(m for m in range(n, -1, -1) if in_order(desired[:m]))
        return desired[m:]
    # keep the longest top part that is already in order; send the rest down, top-most first
    k = next(k for k in range(n + 1) if in_order(desired[k:]))
    return list(reversed(desired[:k]))
