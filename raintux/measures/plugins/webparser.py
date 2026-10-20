"""``Plugin=WebParser``: download a page, parse it with a PCRE ``RegExp``, feed child measures.

Parent: ``URL``, ``RegExp``, ``UpdateRate`` (in skin updates, default 600),
``StringIndex``, ``FinishAction``, ``OnConnectErrorAction``, ``OnRegExpErrorAction``,
``Header``/``HeaderN``, ``UserAgent``, ``CodePage``, ``DecodeCharacterReference``,
``ErrorString``. Child: ``URL=[Parent]`` with ``StringIndex`` (optionally its own
``RegExp`` + ``StringIndex2``) and ``Download=1`` to fetch an image and return its path.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from raintux.core.pcre import compile_pcre
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)

_PARENT = re.compile(r"^\[([^\[\]]+)\]$")
_MAX_BYTES = 16 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RainTux WebParser"


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "raintux" / "webparser"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _decode(raw: bytes, codepage: int, content_type: str) -> str:
    if codepage == 1200:
        return raw.decode("utf-16-le", errors="replace")
    if codepage == 1201:
        return raw.decode("utf-16-be", errors="replace")
    if codepage and codepage != 65001:
        try:
            return raw.decode(f"cp{codepage}", errors="replace")
        except LookupError:
            pass
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        try:
            return raw.decode(m.group(1), errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


def fetch(url: str, headers: dict[str, str], timeout: float = 20.0) -> tuple[bytes, str]:
    """GET ``url`` (http(s) or file); returns (body, content-type)."""
    if re.match(r"^[a-zA-Z]:[\\/]", url) or url.startswith("/"):
        return Path(url.replace("\\", "/")).read_bytes(), ""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - skins fetch arbitrary URLs by design
        body = resp.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise ValueError("response larger than 16 MB")
        return body, resp.headers.get("Content-Type", "")


class WebParserPlugin(Measure):
    bang_namespace = "webparser"

    def read_measure(self) -> None:
        url_raw = self.option_raw("url") or ""
        resolved = self.skin.resolve(url_raw).strip()
        m = _PARENT.match(resolved)
        self.parent_name = m.group(1) if m else None
        self.url = resolved
        self.regexp = self.option_raw("regexp")
        self.string_index = self.get_int("stringindex", 0)
        self.string_index2 = self.get_int("stringindex2", 0)
        self.update_rate = max(1, self.get_int("updaterate", 600))
        self.download = self.get_bool("download")
        self.download_file = self.get("downloadfile")
        self.decode_refs = self.get_int("decodecharacterreference", 0)
        self.codepage = self.get_int("codepage", 0)
        self.error_string = self.get("errorstring")
        if not hasattr(self, "_captures"):
            self._captures: list[str] = []
            self._children: list[WebParserPlugin] = []
            self._ticks = 0
            self._busy = False
            self._linked = False

    # -- parent/child wiring -------------------------------------------------
    def _parent(self) -> WebParserPlugin | None:
        if not self.parent_name:
            return None
        m = self.skin.measure(self.parent_name)
        return m if isinstance(m, WebParserPlugin) else None

    def update_value(self) -> None:
        parent = self._parent()
        if parent is not None:
            if not self._linked:
                parent._children.append(self)
                self._linked = True
                if parent._captures:
                    self._from_parent(parent._captures)
        else:
            if self._ticks % self.update_rate == 0:
                self.refresh()
            self._ticks += 1
        self._raw_number = _number(self.string or "")

    def refresh(self) -> None:
        if self._busy or not self.url:
            return
        self._busy = True
        url = self.url
        headers = {"User-Agent": self.get("useragent") or USER_AGENT}
        i = 1
        while True:
            key = "header" if i == 1 else f"header{i}"
            if not self.has(key):
                break
            name, _sep, value = self.get(key).partition(":")
            if name.strip():
                headers[name.strip()] = value.strip()
            i += 1
        codepage = self.codepage
        pattern = self.skin.resolve(self.regexp) if self.regexp else None

        def work() -> tuple[str, list[str] | None]:
            # Download and match off the UI thread: a slow page or a pathological regex
            # must not freeze the desktop.
            body, ctype = fetch(url, headers)
            text = _decode(body, codepage, ctype)
            if pattern is None:
                return text, [text]
            try:
                m = compile_pcre(pattern).search(text)
            except re.error as e:
                log.warning("[%s] WebParser RegExp: %s", self.name, e)
                return text, None
            return text, ([g if g is not None else "" for g in m.groups()] if m else None)

        self.run_in_background(work, self._fetched)

    def _fetched(self, result: Any) -> None:
        self._busy = False
        if isinstance(result, Exception):
            log.info("[%s] WebParser: %s: %s", self.name, self.url, result)
            if self.error_string:
                self.string = self.error_string
            self.skin.execute(self.get("onconnecterroraction"), self)
            return
        text, captures = result
        if captures is None:
            self.skin.execute(self.get("onregexperroraction"), self)
            if self.error_string:
                self.string = self.error_string
            return
        self._captures = [self._decode_refs(c) for c in captures]
        if self.string_index > 0:
            self.string = self._pick(self._captures, self.string_index)
        elif not self.regexp:
            self.string = text
        else:
            self.string = ""
        self._raw_number = _number(self.string)
        for child in list(self._children):
            child._from_parent(self._captures)
        if self.download and self.string:
            self._start_download(self.string)
        else:
            self.skin.execute(self.get("finishaction"), self)

    def _from_parent(self, captures: list[str]) -> None:
        value = self._pick(captures, self.string_index) if self.string_index > 0 else (captures[0] if captures else "")
        if self.regexp:
            try:
                m = compile_pcre(self.skin.resolve(self.regexp)).search(value)
            except re.error:
                m = None
            if m is None:
                self.skin.execute(self.get("onregexperroraction"), self)
                value = self.error_string or ""
            else:
                groups = [g or "" for g in m.groups()]
                value = self._pick(groups, self.string_index2 or 1)
        value = self._decode_refs(value)
        if self.download and value:
            self._start_download(value)
            return
        self.string = value
        self._raw_number = _number(value)
        self.skin.execute(self.get("finishaction"), self)

    def _start_download(self, url: str) -> None:
        if self.download_file:
            dest_dir = self.skin.root / "DownloadFile"
            dest = dest_dir / Path(self.download_file.replace("\\", "/")).name
        else:
            ext = Path(url.split("?", 1)[0]).suffix[:6] or ".bin"
            dest = cache_dir() / (hashlib.sha1(url.encode()).hexdigest() + ext)

        def work() -> str:
            body, _ctype = fetch(url, {"User-Agent": USER_AGENT})
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(dest)
            return str(dest)

        def done(result: Any) -> None:
            if isinstance(result, Exception):
                log.info("[%s] WebParser download %s: %s", self.name, url, result)
                self.skin.execute(self.get("ondownloaderroraction"), self)
                return
            self.string = result
            self.skin.execute(self.get("finishaction"), self)

        self.run_in_background(work, done)

    def _decode_refs(self, s: str) -> str:
        return html.unescape(s) if self.decode_refs else s

    @staticmethod
    def _pick(items: list[str], index: int) -> str:
        return items[index - 1] if 0 < index <= len(items) else ""

    def command(self, args: str) -> None:
        cmd = args.strip().lower()
        if cmd in ("update", "refetch"):
            target = self._parent() or self
            target.refresh()
        elif cmd in ("reset", "clearresults"):
            self.string = ""
            self._captures = []
        else:
            super().command(args)

    def string_value(self, *args: Any, **kwargs: Any) -> str:
        if self.string is None:
            return ""
        return super().string_value(*args, **kwargs)


def _number(s: str) -> float:
    m = re.match(r"\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", s or "")
    try:
        return float(m.group(0)) if m else 0.0
    except ValueError:
        return 0.0
