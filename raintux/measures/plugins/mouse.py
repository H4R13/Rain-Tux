"""``Measure=Mouse``: mouse move / drag actions (sliders, knobs, drawing).

``MouseMoveAction``, ``LeftMouseDragAction`` (and Middle/Right/X1/X2), the normal mouse
actions while capturing, ``RelativeToSkin`` (default 1), ``RequireDragging``, ``Delay``
(ms between actions, default 16), ``CaptureOverlayColor`` / ``CaptureOverlayCursorName``.
Bangs ``!Mouse:StartCapture`` / ``!Mouse:StopCapture``. Inside the actions, ``$MouseX$``
/ ``$MouseY$`` (and ``:%`` forms) are relative to the skin.
"""

from __future__ import annotations

import time
from typing import Any

from raintux.core.section import parse_color
from raintux.measures.base_measure import Measure

BUTTONS = ("left", "middle", "right", "x1", "x2")


class MouseMeasure(Measure):
    bang_namespace = "mouse"

    def read_measure(self) -> None:
        self.relative = self.get_bool("relativetoskin", True)
        self.require_dragging = self.get_bool("requiredragging")
        self.delay = max(0, self.get_int("delay", 16))
        self.overlay_color = parse_color(self.get("captureoverlaycolor", "0,0,0,0"), (0, 0, 0, 0))
        self.overlay_cursor = self.get("captureoverlaycursorname", "ARROW")
        if not hasattr(self, "dragging"):
            self.dragging: str | None = None
            self.capturing = False
            self._last = 0.0

    def update_value(self) -> None:
        self._raw_number = 1.0 if (self.capturing or self.dragging) else 0.0

    def drag_action(self, button: str) -> str:
        return self.get(f"{button}mousedragaction")

    def wants_drag(self, button: str) -> bool:
        return bool(self.drag_action(button)) and not self.disabled

    def _throttled(self) -> bool:
        now = time.monotonic()
        if (now - self._last) * 1000 < self.delay:
            return True
        self._last = now
        return False

    def _coords(self, x: float, y: float) -> tuple[float, float]:
        if self.relative:
            return x, y
        return x + self.skin.state.x, y + self.skin.state.y

    def moved(self, x: float, y: float, force: bool = False) -> None:
        """Pointer moved (skin coordinates)."""
        if self.disabled or (not force and self._throttled()):
            return
        cx, cy = self._coords(x, y)
        if self.dragging:
            action = self.drag_action(self.dragging)
            if action:
                self.skin.run_mouse_action(action, cx, cy, None, self)
                return
        if not self.require_dragging or self.dragging:
            self.skin.run_mouse_action(self.get("mousemoveaction"), cx, cy, None, self)

    def pressed(self, button: str, x: float, y: float) -> bool:
        if self.wants_drag(button) or self.capturing:
            self.dragging = button
            self.moved(x, y, force=True)
            if self.capturing:
                cx, cy = self._coords(x, y)
                self.skin.run_mouse_action(self.get(f"{button}mousedownaction"), cx, cy, None, self)
            return True
        return False

    def released(self, button: str, x: float, y: float) -> None:
        if self.dragging == button:
            self.dragging = None
        if self.capturing:
            cx, cy = self._coords(x, y)
            self.skin.run_mouse_action(self.get(f"{button}mouseupaction"), cx, cy, None, self)

    def command(self, args: str) -> None:
        w = args.strip().lower()
        host = self.skin.host
        if w == "startcapture" and not self.capturing:
            start = getattr(host, "start_mouse_capture", None)
            if start is not None:
                self.capturing = True
                start(self.skin, self)
        elif w == "stopcapture" and self.capturing:
            self.capturing = False
            self.dragging = None
            stop = getattr(host, "stop_mouse_capture", None)
            if stop is not None:
                stop(self.skin, self)
        else:
            super().command(args)

    def finalize(self) -> None:
        if getattr(self, "capturing", False):
            self.command("stopcapture")


def mouse_measures(skin: Any) -> list[MouseMeasure]:
    return [m for m in skin.measures.values() if isinstance(m, MouseMeasure) and not m.disabled]
