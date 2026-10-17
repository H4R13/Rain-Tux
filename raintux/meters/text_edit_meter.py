"""``Meter=TextEdit``: a String meter the user can type into (current Rainmeter).

Options: InitialText, PlaceholderText (+ PlaceholderFontColor/Face/Size/StringStyle),
MaxLength, Multiline, ReadOnly, Password, InputFilter (NONE/NUMERIC/INTEGER/DECIMAL/
REGEXP + InputRegExp), InputCase, SubmitOnEnter, OnSubmitAction / OnFocusAction /
OnDismissAction (``[$Input]``, ``[$Input:MatchN]``), CaretColor, SelectionColor,
FocusBorderColor / FocusBorderWidth. Bangs: ``!TextEdit:Focus|Submit|Select|SelectAll|
SetText|ScrollByLine|ResetScroll|Clear|Reset``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from raintux.core.formula import parse_number
from raintux.meters import text as T
from raintux.meters.string_meter import StringMeter

log = logging.getLogger(__name__)

_FILTERS = {"numeric": r"[0-9]*", "integer": r"-?[0-9]*", "decimal": r"-?[0-9]*\.?[0-9]*"}
_WORD = re.compile(r"\w+|[^\w\s]+|\s+", re.UNICODE)
MASK = "●"
_BLINK_MS = 530


class TextEditMeter(StringMeter):
    bang_namespace = "textedit"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.edit_text = ""
        self.caret = 0
        self.anchor = 0
        self.focused = False
        self.scroll_x = 0.0
        self.scroll_y = 0.0
        self._initial: str | None = None
        self._undo: list[tuple[str, int, int]] = []
        self._redo: list[tuple[str, int, int]] = []
        self._last_kind = ""
        self._submitted: str | None = None
        self._blink_on = True
        self._blink_handle: Any = None

    # ------------------------------------------------------------------ options
    def read_meter(self) -> None:
        super().read_meter()
        self.clip = 0
        self.max_length = max(0, self.get_int("maxlength", 0))
        self.multiline = self.get_bool("multiline")
        self.read_only = self.get_bool("readonly")
        self.password = self.get_bool("password")
        filt = self.get("inputfilter", "NONE").strip().lower()
        self.filter_rx: re.Pattern[str] | None = None
        if filt == "regexp":
            pattern = self.get("inputregexp", ".*")
        else:
            pattern = _FILTERS.get(filt, "")
        if pattern:
            from raintux.core.pcre import compile_pcre

            try:
                self.filter_rx = compile_pcre(pattern)
            except re.error as e:
                log.warning("[%s] InputRegExp: %s", self.name, e)
        self.input_case = self.get("inputcase", "none").strip().lower()
        self.submit_on_enter = self.get_bool("submitonenter", not self.multiline)
        self.placeholder = self.get("placeholdertext")
        dim = (self.color_[0], self.color_[1], self.color_[2], self.color_[3] * 0.4)
        self.placeholder_color = self.get_color("placeholderfontcolor", dim)
        self.placeholder_face = self.get("placeholderfontface") or self.face
        self.placeholder_size = self.get_number("placeholderfontsize", self.size)
        pstyle = self.get("placeholderstringstyle", "").lower()
        self.placeholder_weight = (700 if "bold" in pstyle else 400) if pstyle else self.weight
        self.placeholder_italic = ("italic" in pstyle) if pstyle else self.italic
        self.caret_color = self.get_color("caretcolor", self.color_)
        self.selection_color = self.get_color("selectioncolor", (53 / 255, 132 / 255, 228 / 255, 0.5))
        self.focus_border = self.get_color("focusbordercolor", (0, 0, 0, 0))
        self.focus_border_width = max(0.0, self.get_number("focusborderwidth", 1.0))
        initial = self.get("initialtext")
        if initial != self._initial:
            self._initial = initial
            self.edit_text = self._transform(initial)
            self.caret = self.anchor = 0
            self._undo.clear()
            self._redo.clear()
        else:
            self.edit_text = self._transform(self.edit_text)
            self.caret = min(self.caret, len(self.edit_text))
            self.anchor = min(self.anchor, len(self.edit_text))

    def _transform(self, text: str) -> str:
        if not self.multiline:
            text = text.replace("\r", "").replace("\n", " ")
        if self.max_length and len(text) > self.max_length:
            text = text[: self.max_length]
        return T.apply_case(text, self.case)

    # ------------------------------------------------------------------ content
    @property
    def accepts_input(self) -> bool:
        return not self.read_only

    def display_text(self) -> str:
        return MASK * len(self.edit_text) if self.password else self.edit_text

    def showing_placeholder(self) -> bool:
        return not self.edit_text and bool(self.placeholder)

    def build_text(self) -> str:
        return self.placeholder if self.showing_placeholder() else self.display_text()

    def _style(self) -> T.TextStyle:
        if self.showing_placeholder():
            return T.TextStyle(face=self.placeholder_face, size=self.placeholder_size, weight=self.placeholder_weight,
                               italic=self.placeholder_italic, color=self.placeholder_color, h_align=self.h_align,
                               v_align=self.v_align, width=self._wrap_width(), wrap=self.multiline)
        return T.TextStyle(face=self.face, size=self.size, weight=self.weight, italic=self.italic, color=self.color_,
                           h_align=self.h_align, v_align=self.v_align, width=self._wrap_width(), wrap=self.multiline)

    def _wrap_width(self) -> float | None:
        if self.multiline and self.w_opt:
            return max(1.0, self.w_opt)
        return None

    def update_meter(self) -> None:
        self.text = self.build_text()
        self.style = self._style()
        w, h = T.measure(self.text or " ", self.style)
        self.natural_size(w + 2, h)

    def input_variable(self, rest: str) -> str | None:
        """``[$Input]`` / ``[$Input:MatchN]`` for actions of this meter."""
        if not rest:
            return self.edit_text
        m = re.fullmatch(r":match(\d+)", rest.strip().lower())
        if m is None:
            return None
        group = int(m.group(1))
        if self.filter_rx is None:
            return self.edit_text if group == 0 else ""
        fm = self.filter_rx.fullmatch(self.edit_text)
        if fm is None or group > (fm.re.groups or 0):
            return ""
        return fm.group(group) or ""

    # ------------------------------------------------------------------ geometry
    def _layout(self, cr: Any = None) -> tuple[Any, float, float]:
        """Pango layout of the drawn text plus its origin inside the content box."""
        if not T.HAVE_PANGO:
            return None, 0.0, 0.0
        layout = T.make_layout(cr if cr is not None else T._ctx(), self.text or "", self.style)
        _ink, logical = layout.get_pixel_extents()
        ox = 1.0
        if not self.multiline:
            if self.h_align == "center":
                ox = max(1.0, (self.content_w - logical.width) / 2)
            elif self.h_align == "right":
                ox = max(1.0, self.content_w - logical.width - 1)
        oy = 0.0
        if self.v_align == "center":
            oy = (self.content_h - logical.height) / 2
        elif self.v_align == "bottom":
            oy = self.content_h - logical.height
        return layout, ox - self.scroll_x, oy - self.scroll_y

    def _byte(self, index: int) -> int:
        return len(self.display_text()[:index].encode("utf-8"))

    def _char(self, byte_index: int) -> int:
        return len(self.display_text().encode("utf-8")[:byte_index].decode("utf-8", errors="ignore"))

    def caret_rect(self, index: int | None = None) -> tuple[float, float, float]:
        """(x, y, height) of the caret in content coordinates."""
        layout, ox, oy = self._layout()
        if layout is None or self.showing_placeholder():
            h = self.size * 96 / 72 * 1.2
            return (ox, oy, h)
        from raintux.meters.text import Pango

        pos = layout.index_to_pos(self._byte(self.caret if index is None else index))
        return (ox + pos.x / Pango.SCALE, oy + pos.y / Pango.SCALE, max(1.0, pos.height / Pango.SCALE))

    def index_at(self, x: float, y: float) -> int:
        """Character index nearest the content-relative point."""
        layout, ox, oy = self._layout()
        if layout is None or not self.edit_text:
            return 0
        from raintux.meters.text import Pango

        _inside, idx, trailing = layout.xy_to_index(int((x - ox) * Pango.SCALE), int((y - oy) * Pango.SCALE))
        return min(len(self.edit_text), self._char(idx) + (1 if trailing else 0))

    def _ensure_caret_visible(self) -> None:
        if self.multiline:
            cx, cy, ch = self.caret_rect()
            if cy < 0:
                self.scroll_y += cy
            elif cy + ch > self.content_h:
                self.scroll_y += cy + ch - self.content_h
            self.scroll_y = max(0.0, self.scroll_y)
            return
        cx, _cy, _ch = self.caret_rect()
        if cx > self.content_w - 2:
            self.scroll_x += cx - (self.content_w - 2)
        elif cx < 1:
            self.scroll_x = max(0.0, self.scroll_x + cx - 1)

    # ------------------------------------------------------------------ drawing
    def draw_content(self, cr: Any) -> None:
        cr.save()
        cr.rectangle(0, 0, self.content_w, self.content_h)
        cr.clip()
        layout, ox, oy = self._layout(cr)
        if layout is not None:
            from raintux.meters.text import Pango, PangoCairo

            if self.focused and self.caret != self.anchor and not self.showing_placeholder():
                a, b = sorted((self.caret, self.anchor))
                cr.set_source_rgba(*self.selection_color)
                for line_rect in _selection_rects(layout, self._byte(a), self._byte(b)):
                    x0, y0, w0, h0 = line_rect
                    cr.rectangle(ox + x0, oy + y0, w0, h0)
                cr.fill()
            cr.move_to(ox, oy)
            cr.set_source_rgba(*self.style.color)
            PangoCairo.show_layout(cr, layout)
            if self.focused and self._blink_on and not self.read_only:
                pos = layout.index_to_pos(0 if self.showing_placeholder() else self._byte(self.caret))
                x = round(ox + pos.x / Pango.SCALE) + 0.5
                y = oy + pos.y / Pango.SCALE
                cr.set_source_rgba(*self.caret_color)
                cr.set_line_width(1)
                cr.move_to(x, y)
                cr.line_to(x, y + max(1.0, pos.height / Pango.SCALE))
                cr.stroke()
        cr.restore()
        if self.focused and self.focus_border[3] > 0 and self.focus_border_width > 0:
            half = self.focus_border_width / 2
            cr.set_source_rgba(*self.focus_border)
            cr.set_line_width(self.focus_border_width)
            cr.rectangle(-self.padding[0] + half, -self.padding[1] + half,
                         self.w - self.focus_border_width, self.h - self.focus_border_width)
            cr.stroke()

    # ------------------------------------------------------------------ focus
    def focus(self) -> None:
        if self.focused or not self.accepts_input:
            return
        current = self.skin.focused_input
        if current is not None and current is not self:
            current.dismiss()
        self.focused = True
        self.skin.focused_input = self
        setter = getattr(self.skin.host, "set_keyboard_focus", None)
        if setter:
            setter(self.skin, True)
        self._restart_blink()
        self.skin.execute(self.get("onfocusaction"), self)
        self.skin.host.redraw(self.skin)

    def dismiss(self, run_action: bool = True) -> None:
        if not self.focused:
            return
        self.focused = False
        if self.skin.focused_input is self:
            self.skin.focused_input = None
            setter = getattr(self.skin.host, "set_keyboard_focus", None)
            if setter:
                setter(self.skin, False)
        self.anchor = self.caret
        if self._blink_handle is not None:
            self.skin.host.cancel(self._blink_handle)
            self._blink_handle = None
        if run_action and self.edit_text != self._submitted:
            self.skin.execute(self.get("ondismissaction"), self)
        self.skin.host.redraw(self.skin)

    def submit(self) -> None:
        self._submitted = self.edit_text
        self.skin.execute(self.get("onsubmitaction"), self)

    def _restart_blink(self) -> None:
        self._blink_on = True
        host = self.skin.host
        if self._blink_handle is not None:
            host.cancel(self._blink_handle)

        def tick() -> None:
            if not self.focused or self.skin.closed:
                self._blink_handle = None
                return
            self._blink_on = not self._blink_on
            host.redraw(self.skin)
            self._blink_handle = host.call_later(_BLINK_MS, tick)

        self._blink_handle = host.call_later(_BLINK_MS, tick)

    def _changed(self) -> None:
        self.update_meter()
        self._ensure_caret_visible()
        self._restart_blink()
        self.skin.layout()
        self.skin.host.redraw(self.skin)

    # ------------------------------------------------------------------ editing
    def _snapshot(self, kind: str) -> None:
        if kind != self._last_kind or kind == "other" or not self._undo:
            self._undo.append((self.edit_text, self.caret, self.anchor))
            del self._undo[:-200]
        self._redo.clear()
        self._last_kind = kind

    def _accepts(self, text: str) -> bool:
        return self.filter_rx is None or self.filter_rx.fullmatch(text) is not None

    def insert(self, text: str) -> bool:
        if not self.focused or self.read_only or not text:
            return False
        if not self.multiline:
            text = text.replace("\r", "").replace("\n", " ")
        if self.input_case in ("upper", "lower"):
            text = T.apply_case(text, self.input_case)
        a, b = sorted((self.caret, self.anchor))
        room = (self.max_length - (len(self.edit_text) - (b - a))) if self.max_length else len(text)
        text = text[: max(0, room)]
        if not text:
            return False
        new = self.edit_text[:a] + text + self.edit_text[b:]
        if not self._accepts(new):
            return False
        self._snapshot("typing")
        self.edit_text = new
        self.caret = self.anchor = a + len(text)
        self._changed()
        return True

    def delete(self, forward: bool, word: bool = False) -> bool:
        if not self.focused or self.read_only:
            return False
        a, b = sorted((self.caret, self.anchor))
        if a == b:
            if word:
                target = self._word_boundary(a, forward)
            else:
                target = a + 1 if forward else a - 1
            target = max(0, min(len(self.edit_text), target))
            a, b = sorted((a, target))
            if a == b:
                return False
        self._snapshot("deleting")
        self.edit_text = self.edit_text[:a] + self.edit_text[b:]
        self.caret = self.anchor = a
        self._changed()
        return True

    def _word_boundary(self, pos: int, forward: bool) -> int:
        spans = [m.span() for m in _WORD.finditer(self.edit_text) if not m.group().isspace()]
        if forward:
            return next((s for s, _e in spans if s > pos), len(self.edit_text))
        return next((s for s, _e in reversed(spans) if s < pos), 0)

    def move(self, pos: int, extend: bool) -> None:
        self.caret = max(0, min(len(self.edit_text), pos))
        if not extend:
            self.anchor = self.caret
        self._last_kind = ""
        self._changed()

    def select(self, start: int, length: int = -1) -> None:
        start = max(0, min(len(self.edit_text), start))
        end = len(self.edit_text) if length < 0 else max(0, min(len(self.edit_text), start + length))
        self.anchor, self.caret = start, end
        self._changed()

    def select_word_at(self, pos: int) -> None:
        for m in _WORD.finditer(self.edit_text):
            if m.start() <= pos <= m.end():
                self.anchor, self.caret = m.start(), m.end()
                break
        self._changed()

    def set_text(self, text: str) -> None:
        self._snapshot("other")
        self.edit_text = self._transform(text)
        self.caret = self.anchor = len(self.edit_text)
        self._changed()

    def undo(self, redo: bool = False) -> None:
        src, dst = (self._redo, self._undo) if redo else (self._undo, self._redo)
        if not src:
            return
        dst.append((self.edit_text, self.caret, self.anchor))
        self.edit_text, self.caret, self.anchor = src.pop()
        self._last_kind = ""
        self._changed()

    def selected_text(self) -> str:
        a, b = sorted((self.caret, self.anchor))
        return self.edit_text[a:b]

    def handle_key(self, key: str, ctrl: bool, shift: bool) -> bool:
        """Editing keys (GDK key names). Printable input arrives through :meth:`insert`."""
        k = key.lower()
        if k in ("return", "kp_enter"):
            if self.multiline and not (self.submit_on_enter and not shift):
                return self.insert("\n")
            if self.submit_on_enter or not self.multiline:
                self.submit()
                return True
            return False
        if k == "escape":
            self.dismiss()
            return True
        if k == "backspace":
            return self.delete(False, ctrl)
        if k == "delete":
            return self.delete(True, ctrl)
        if k in ("left", "kp_left"):
            self.move(self._word_boundary(self.caret, False) if ctrl else self.caret - 1, shift)
            return True
        if k in ("right", "kp_right"):
            self.move(self._word_boundary(self.caret, True) if ctrl else self.caret + 1, shift)
            return True
        if k in ("home", "kp_home"):
            self.move(0 if (ctrl or not self.multiline) else self.edit_text.rfind("\n", 0, self.caret) + 1, shift)
            return True
        if k in ("end", "kp_end"):
            if ctrl or not self.multiline:
                self.move(len(self.edit_text), shift)
            else:
                nl = self.edit_text.find("\n", self.caret)
                self.move(len(self.edit_text) if nl < 0 else nl, shift)
            return True
        if k in ("up", "down") and self.multiline:
            x, y, h = self.caret_rect()
            self.move(self.index_at(x, y - h / 2 if k == "up" else y + h * 1.5), shift)
            return True
        if ctrl and k == "a":
            self.select(0)
            return True
        if ctrl and k in ("c", "x"):
            text = self.selected_text()
            if text and not self.password:
                setter = getattr(self.skin.host, "clipboard_set", None)
                if setter:
                    setter(text)
                if k == "x":
                    self.delete(False)
            return True
        if ctrl and k == "v":
            getter = getattr(self.skin.host, "clipboard_get", None)
            if getter:
                getter(lambda text: text and self.insert(text))
            return True
        if ctrl and k == "z":
            self.undo(redo=shift)
            return True
        if ctrl and k == "y":
            self.undo(redo=True)
            return True
        return False

    # ------------------------------------------------------------------ bangs
    def command(self, args: str) -> None:
        word, _sep, rest = args.strip().partition(" ")
        w = word.lower()
        if w == "focus":
            self.focus()
        elif w == "submit":
            self.submit()
        elif w == "select":
            nums = [int(parse_number(v, 0)) for v in rest.split()]
            self.select(nums[0] if nums else 0, nums[1] if len(nums) > 1 else -1)
        elif w == "selectall":
            self.select(0)
        elif w == "settext":
            self.set_text(rest.strip().strip('"'))
        elif w == "scrollbyline":
            n = int(parse_number(rest, 0))
            self.scroll_y = max(0.0, self.scroll_y + n * self.size * 96 / 72 * 1.2)
            self.skin.host.redraw(self.skin)
        elif w == "resetscroll":
            self.scroll_x = self.scroll_y = 0.0
            self.skin.host.redraw(self.skin)
        elif w == "clear":
            self.set_text("")
        elif w == "reset":
            self.set_text(self._initial or "")
            self.caret = self.anchor = 0
        else:
            log.info("[%s] unknown TextEdit command %r", self.name, args)

    def hit_content(self, x: float, y: float) -> bool:
        return True


def _selection_rects(layout: Any, start: int, end: int) -> list[tuple[float, float, float, float]]:
    """Pixel rectangles covering byte range [start, end) of a Pango layout, one per line."""
    from raintux.meters.text import Pango

    rects = []
    it = layout.get_iter()
    while True:
        line = it.get_line_readonly()
        ls = line.start_index
        le = ls + line.length
        a, b = max(start, ls), min(end, le)
        if a < b or (start <= ls < end and line.length == 0):
            _ink, logical = it.get_line_extents()
            ranges = line.get_x_ranges(a, b)
            for i in range(0, len(ranges) - 1, 2):
                x0, x1 = ranges[i], ranges[i + 1]
                rects.append((x0 / Pango.SCALE, logical.y / Pango.SCALE, max(1.0, (x1 - x0) / Pango.SCALE),
                              logical.height / Pango.SCALE))
        if not it.next_line():
            break
    return rects
