"""``Meter=String``: text with measures, alignment, clipping, inline styles and effects."""

from __future__ import annotations

import logging
import re
from typing import Any

from raintux.core.formula import parse_number
from raintux.core.section import parse_color
from raintux.measures.base_measure import pcre_to_python
from raintux.meters import text as T
from raintux.meters.base_meter import Meter

log = logging.getLogger(__name__)

_PERCENT_N = re.compile(r"%(\d)")


def parse_auto_scale(value: str) -> int:
    """``AutoScale``: 0 off, 1 = 1024, 2 = 1000, ``1k`` = 3, ``2k`` = 4."""
    v = value.strip().lower()
    return {"1": 1, "2": 2, "1k": 3, "2k": 4}.get(v, 0)


class StringMeter(Meter):
    # Defaults for [Meter:X] lookups from sections read before this meter.
    h_align = "left"
    v_align = "top"
    text_override: str | None = None  # Lua meter:SetText, until the options are next read

    def read_meter(self) -> None:
        self.face = self.get("fontface", "Arial") or "Arial"
        self.size = self.get_number("fontsize", 10)
        self.color_ = self.get_color("fontcolor", (0, 0, 0, 1))
        style = self.get("stringstyle", "normal").lower()
        self.weight = 700 if "bold" in style else 400
        self.italic = "italic" in style
        if self.has("fontweight"):
            self.weight = self.get_int("fontweight", self.weight)
        align = self.get("stringalign", "left").lower()
        self.h_align = "center" if align.startswith("center") else "right" if align.startswith("right") else "left"
        rest = align[len(self.h_align):] if align.startswith(self.h_align) else ""
        self.v_align = "center" if rest == "center" else "bottom" if rest == "bottom" else "top"
        self.case = self.get("stringcase", "none")
        self.effect = self.get("stringeffect", "none").lower()
        self.effect_color = self.get_color("fonteffectcolor", (0, 0, 0, 1))
        self.clip = self.get_int("clipstring", 0)
        self.clip_w = self.get_number("clipstringw", -1) if self.has("clipstringw") else None
        self.clip_h = self.get_number("clipstringh", -1) if self.has("clipstringh") else None
        self.auto_scale = parse_auto_scale(self.get("autoscale", "0"))
        self.scale = self.get_number("scale", 1.0) or 1.0
        self.decimals = self.get_int("numofdecimals", -1) if self.has("numofdecimals") else None
        self.percentual = self.get_bool("percentual")
        self.angle = self.get_number("angle", 0.0)
        self.inline_specs: list[tuple[str, str]] = []
        i = 1
        while True:
            sfx = "" if i == 1 else str(i)
            setting = self.opts.get("inlinesetting" + sfx)
            if setting is None:
                break
            self.inline_specs.append((setting, self.opts.get("inlinepattern" + sfx, ".*")))
            i += 1
        self.text = ""
        self.text_override = None
        self.style = T.TextStyle()
        self.shadows: list[tuple[int, int, float, float, Any]] = []

    def set_text(self, text: str) -> None:
        """Lua ``meter:SetText``: replaces the Text option as given (no variables, ``%1`` still
        filled in) until the meter re-reads its options, so DynamicVariables=1 undoes it."""
        self.text_override = text

    # -- content -----------------------------------------------------------------
    def measure_text(self, index: int) -> str:
        m = self.measure_value(index)
        if m is None:
            return ""
        if self.decimals is not None:
            d = self.decimals
        else:
            d = 1 if self.auto_scale else 0
        return m.string_value(d, self.auto_scale, self.scale, self.percentual)

    def build_text(self) -> str:
        if self.text_override is not None:
            text = self.text_override
        else:
            raw = self.option_raw("text")
            if raw is None:
                raw = "%1" if self.measures else ""
            text = self.skin.resolve(raw, section_vars=True)
        if self.measures:
            text = _PERCENT_N.sub(lambda m: self.measure_text(int(m.group(1)) - 1) if 0 < int(m.group(1)) <= len(self.measures) else m.group(0), text)
        prefix = self.skin.resolve(self.option_raw("prefix") or "", section_vars=True)
        postfix = self.skin.resolve(self.option_raw("postfix") or "", section_vars=True)
        return T.apply_case(prefix + text + postfix, self.case)

    def _inline(self, text: str) -> tuple[list[tuple[int, int, str, Any]], list[tuple[int, int, float, float, Any]]]:
        """``InlineSetting=Type | param | param…`` applied to ``InlinePattern`` matches (or their groups)."""
        attrs: list[tuple[int, int, str, Any]] = []
        shadows: list[tuple[int, int, float, float, Any]] = []

        def num(value: str, default: float) -> float:  # parameters may be formulas: Size | (12*#Scale#)
            return parse_number(value, default, self.skin.measure_number) if value.strip() else default

        for setting, pattern in self.inline_specs:
            parts = [p.strip() for p in setting.split("|")]
            kind, params = parts[0].lower(), parts[1:]
            p0 = params[0] if params else ""
            try:
                rx = re.compile(pcre_to_python(pattern or ".*"), re.S)
            except re.error as e:
                log.warning("[%s] InlinePattern %r: %s", self.name, pattern, e)
                continue
            spans = []
            for m in rx.finditer(text):
                if m.re.groups:
                    spans += [m.span(g) for g in range(1, m.re.groups + 1) if m.span(g)[0] >= 0]
                else:
                    spans.append(m.span())
            try:
                for start, end in spans:
                    if end <= start:
                        continue
                    if kind == "color":
                        attrs.append((start, end, "color", parse_color(p0, self.color_)))
                    elif kind == "gradientcolor":
                        first = params[1].split(";")[0] if len(params) > 1 else ""
                        attrs.append((start, end, "color", parse_color(first, self.color_)))
                    elif kind == "face":
                        attrs.append((start, end, "face", p0))
                    elif kind == "size":
                        attrs.append((start, end, "size", num(p0, self.size)))
                    elif kind == "weight":
                        attrs.append((start, end, "weight", int(num(p0, 400))))
                    elif kind in ("italic", "oblique", "underline", "strikethrough"):
                        attrs.append((start, end, kind, True))
                    elif kind == "characterspacing":
                        trail = params[1] if len(params) > 1 else p0
                        attrs.append((start, end, "spacing", num(trail, 0)))
                    elif kind == "stretch":
                        attrs.append((start, end, "stretch", max(0, min(8, int(num(p0, 5)) - 1))))
                    elif kind == "shadow":
                        off = [v.strip() for v in p0.split(",")]
                        sx = num(off[0], 1.0)
                        sy = num(off[1], 1.0) if len(off) > 1 else 1.0
                        col = parse_color(params[2], (0, 0, 0, 1)) if len(params) > 2 else (0, 0, 0, 1)
                        shadows.append((start, end, sx, sy, col))
                    elif kind == "typography":
                        low = p0.lower()
                        if "superscript" in low:
                            attrs.append((start, end, "rise", self.size * 0.35))
                            attrs.append((start, end, "scale", 0.7))
                        elif "subscript" in low:
                            attrs.append((start, end, "rise", -self.size * 0.15))
                            attrs.append((start, end, "scale", 0.7))
            except ValueError as e:
                log.warning("[%s] InlineSetting %r: %s", self.name, setting, e)
        return attrs, shadows

    def update_meter(self) -> None:
        text = self.build_text()
        for setting, pattern in self.inline_specs:
            parts = [p.strip() for p in setting.split("|")]
            if parts[0].lower() == "case" and len(parts) > 1:
                try:
                    text = re.sub(pcre_to_python(pattern or ".*"), lambda m: T.apply_case(m.group(0), parts[1]), text, flags=re.S)
                except re.error:
                    pass
        self.text = text
        inline, self.shadows = self._inline(text) if self.inline_specs else ([], [])
        style = T.TextStyle(
            face=self.face, size=self.size, weight=self.weight, italic=self.italic, color=self.color_,
            h_align=self.h_align, v_align=self.v_align, inline=inline,
        )
        legacy_pad = 0.0 if self.skin.accurate_text else (self.size * 96 / 72) / 6.0
        if self.clip == 2:
            limit_w = self.clip_w if self.clip_w is not None and self.clip_w > 0 else self.w_opt
            style.wrap = True
            style.ellipsize = True
            nat_w, _ = T.measure(text, style)
            width = min(nat_w, limit_w) if limit_w else nat_w
            style.width = max(1.0, width)
            limit_h = self.clip_h if self.clip_h is not None and self.clip_h > 0 else self.h_opt
            style.height = limit_h
            w, h = T.measure(text, style)
            if limit_h:
                h = min(h, limit_h)
            self.style = style
            self.content_w = (self.w_opt if self.w_opt is not None and self.clip_w is None else w + 2 * legacy_pad)
            self.content_h = self.h_opt if self.h_opt is not None and self.clip_h is None else h
            return
        # AccurateText=0 shifts the text by legacy_pad but keeps the full W for wrapping
        # and clipping (Rainmeter's Canvas::GetTextDrawPosition).
        if self.clip == 1 and self.w_opt:
            style.width = max(1.0, self.w_opt)
            style.ellipsize = True
            style.wrap = bool(self.h_opt)
            style.height = self.h_opt
        elif self.w_opt:
            style.width = max(1.0, self.w_opt)
        self.style = style
        w, h = T.measure(text, style)
        self.natural_size(w + 2 * legacy_pad, h)

    def box(self) -> tuple[float, float, float, float]:
        w, h = self.w, self.h
        x = self.x - (w / 2 if self.h_align == "center" else w if self.h_align == "right" else 0)
        y = self.y - (h / 2 if self.v_align == "center" else h if self.v_align == "bottom" else 0)
        return (x, y, w, h)

    def _draw_self(self, cr: Any) -> None:
        if self.angle:
            cr.save()
            cr.translate(self.x, self.y)
            cr.rotate(self.angle)
            cr.translate(-self.x, -self.y)
            super()._draw_self(cr)
            cr.restore()
        else:
            super()._draw_self(cr)

    def draw_content(self, cr: Any) -> None:
        if not self.text:
            return
        legacy_pad = 0.0 if self.skin.accurate_text else (self.size * 96 / 72) / 6.0
        x = {"left": legacy_pad, "right": -legacy_pad}.get(self.h_align, 0.0)
        w = self.content_w
        if self.clip:
            cr.rectangle(0, 0, self.content_w, self.content_h)
            cr.clip()
        T.draw(cr, self.text, self.style, x, 0, w, self.content_h,
               effect=self.effect if self.effect in ("shadow", "border") else "",
               effect_color=self.effect_color, shadows=self.shadows)
