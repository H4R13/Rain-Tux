"""Media plugins: NowPlaying (MPRIS over D-Bus), Win7AudioPlugin (volume), AudioLevel
(visualizer data from the audio monitor) and MediaKey."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import shutil
import struct
import subprocess
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from raintux.core.formula import parse_number
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


# --------------------------------------------------------------------------- MPRIS
class Mpris:
    """Session-bus MPRIS players; proxies cache properties and follow PropertiesChanged."""

    _instance: Mpris | None = None

    @classmethod
    def get(cls) -> Mpris | None:
        if cls._instance is None:
            try:
                cls._instance = cls()
            except Exception:
                log.info("MPRIS unavailable (no session bus?)", exc_info=True)
                return None
        return cls._instance

    def __init__(self) -> None:
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.names: list[str] = []
        self.proxies: dict[str, Any] = {}
        self._refresh_names()
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self._owner_changed)

    def _refresh_names(self) -> None:
        res = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                                 None, None, self.Gio.DBusCallFlags.NONE, 500, None)
        self.names = sorted(n for n in res.unpack()[0] if n.startswith(MPRIS_PREFIX))

    def _owner_changed(self, _conn: Any, _sender: Any, _path: Any, _iface: Any, _sig: Any, params: Any) -> None:
        name, _old, new = params.unpack()
        if not name.startswith(MPRIS_PREFIX):
            return
        if new and name not in self.names:
            self.names.append(name)
            self.names.sort()
        elif not new and name in self.names:
            self.names.remove(name)
            self.proxies.pop(name, None)

    def proxy(self, name: str) -> Any:
        p = self.proxies.get(name)
        if p is None:
            p = self.Gio.DBusProxy.new_sync(self.bus, self.Gio.DBusProxyFlags.NONE, None, name,
                                            "/org/mpris/MediaPlayer2", PLAYER_IFACE, None)
            self.proxies[name] = p
        return p

    def pick(self, preferred: str) -> str | None:
        """Bus name for ``PlayerName`` (substring match), else the playing player, else the first."""
        if not self.names:
            return None
        pref = preferred.strip().lower()
        aliases = {"wmp": "", "itunes": "", "winamp": "", "cad": "", "wlm": "", "aimp": "aimp", "foobar2000": "foobar"}
        pref = aliases.get(pref, pref)
        if pref:
            for n in self.names:
                if pref in n[len(MPRIS_PREFIX):].lower():
                    return n
        for n in self.names:
            if self.prop(n, "PlaybackStatus") == "Playing":
                return n
        return self.names[0]

    def prop(self, name: str, key: str) -> Any:
        v = self.proxy(name).get_cached_property(key)
        return v.unpack() if v is not None else None

    def position_us(self, name: str) -> int:
        try:
            res = self.bus.call_sync(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                                     self.GLib.Variant("(ss)", (PLAYER_IFACE, "Position")), None,
                                     self.Gio.DBusCallFlags.NONE, 200, None)
            return int(res.unpack()[0])
        except Exception:
            return 0

    def call(self, name: str, method: str, sig: str | None = None, args: tuple[Any, ...] = ()) -> None:
        params = self.GLib.Variant(sig, args) if sig else None
        self.proxy(name).call(method, params, self.Gio.DBusCallFlags.NONE, 1000, None, None, None)

    def set_prop(self, name: str, key: str, variant: Any) -> None:
        self.bus.call(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Set",
                      self.GLib.Variant("(ssv)", (PLAYER_IFACE, key, variant)), None,
                      self.Gio.DBusCallFlags.NONE, 1000, None, None, None)

    def quit(self, name: str) -> None:
        self.bus.call(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2", "Quit", None, None,
                      self.Gio.DBusCallFlags.NONE, 1000, None, None, None)


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


_cover_cache: dict[str, str] = {}


class NowPlayingPlugin(Measure):
    """``PlayerName`` (or ``[ParentMeasure]``) + ``PlayerType`` = Title, Artist, Album, Cover, Duration,
    Position, Progress, State, Status, Volume, Rating, Repeat, Shuffle, Number, Year, Genre, File."""

    bang_namespace = "nowplaying"

    def read_measure(self) -> None:
        raw = self.skin.resolve(self.option_raw("playername") or "").strip()
        m = re.match(r"^\[([^\[\]]+)\]$", raw)
        self.parent_name = m.group(1) if m else None
        self.player_pref = "" if m else raw
        self.ptype = self.get("playertype", "Title").strip().lower()
        self.default_cover = self.get("defaultcover")
        if self.ptype in ("progress", "volume") and not self.has("maxvalue"):
            self.max_value = 100.0
        if self.ptype == "rating" and not self.has("maxvalue"):
            self.max_value = 5.0
        self.bus_name: str | None = None

    def _player(self) -> str | None:
        mp = Mpris.get()
        if mp is None:
            return None
        if self.parent_name:
            parent = self.skin.measure(self.parent_name)
            if isinstance(parent, NowPlayingPlugin):
                return parent.bus_name or mp.pick(parent.player_pref)
        return mp.pick(self.player_pref)

    def update_value(self) -> None:
        mp = Mpris.get()
        name = self._player()
        self.bus_name = name
        t = self.ptype
        if mp is None or name is None:
            self._raw_number = 0.0
            self.string = None if t in ("duration", "position", "progress", "state", "status", "volume", "rating",
                                        "repeat", "shuffle") else ""
            if t == "cover":
                self.string = self.skin.resolve(self.default_cover) if self.default_cover else ""
            if t in ("duration", "position"):
                self.string = "0:00"
            return
        meta = mp.prop(name, "Metadata") or {}
        length = float(meta.get("mpris:length", 0) or 0) / 1e6
        num: float = 0.0
        s: str | None = None
        if t == "title":
            s = str(meta.get("xesam:title", ""))
        elif t == "artist":
            a = meta.get("xesam:artist") or meta.get("xesam:albumArtist") or ""
            s = ", ".join(a) if isinstance(a, (list, tuple)) else str(a)
        elif t == "album":
            s = str(meta.get("xesam:album", ""))
        elif t == "genre":
            g = meta.get("xesam:genre") or ""
            s = ", ".join(g) if isinstance(g, (list, tuple)) else str(g)
        elif t == "number":
            num = float(meta.get("xesam:trackNumber", 0) or 0)
        elif t == "year":
            m = re.match(r"(\d{4})", str(meta.get("xesam:contentCreated", "")))
            num = float(m.group(1)) if m else 0.0
        elif t == "file":
            url = str(meta.get("xesam:url", ""))
            s = urllib.parse.unquote(url[7:]) if url.startswith("file://") else url
        elif t == "cover":
            s = self._cover(str(meta.get("mpris:artUrl", "")))
        elif t == "duration":
            num, s = length, _mmss(length)
        elif t in ("position", "progress"):
            pos = mp.position_us(name) / 1e6
            if t == "position":
                num, s = pos, _mmss(pos)
            else:
                num = (pos / length * 100.0) if length > 0 else 0.0
        elif t == "state":
            num = {"Playing": 1.0, "Paused": 2.0}.get(mp.prop(name, "PlaybackStatus") or "", 0.0)
        elif t == "status":
            num = 1.0
        elif t == "volume":
            vol = mp.prop(name, "Volume")
            num = float(vol) * 100.0 if vol is not None else 0.0
        elif t == "repeat":
            num = 0.0 if (mp.prop(name, "LoopStatus") or "None") == "None" else 1.0
        elif t == "shuffle":
            num = 1.0 if mp.prop(name, "Shuffle") else 0.0
        elif t == "rating":
            r = meta.get("xesam:userRating", meta.get("rating", 0)) or 0
            num = float(r) * 5.0 if float(r) <= 1.0 else float(r)
        self._raw_number = num
        self.string = s

    def _cover(self, art: str) -> str:
        default = self.skin.resolve(self.default_cover) if self.default_cover else ""
        if not art:
            return default
        if art.startswith("file://"):
            return urllib.parse.unquote(art[7:])
        if art in _cover_cache:
            return _cover_cache[art] or default
        from raintux.measures.plugins.webparser import cache_dir, fetch

        dest = cache_dir() / ("cover-" + hashlib.sha1(art.encode()).hexdigest() + ".img")
        if dest.exists():
            _cover_cache[art] = str(dest)
            return str(dest)
        _cover_cache[art] = ""

        def work() -> str:
            body, _ct = fetch(art, {})
            dest.write_bytes(body)
            return str(dest)

        def done(result: Any) -> None:
            _cover_cache[art] = "" if isinstance(result, Exception) else result

        self.run_in_background(work, done)
        return default

    def command(self, args: str) -> None:
        mp = Mpris.get()
        name = self._player()
        if mp is None or name is None:
            if args.strip().lower() == "openplayer":
                self._open_player()
            return
        word, _sep, arg = args.strip().partition(" ")
        w = word.lower()
        simple = {"play": "Play", "pause": "Pause", "playpause": "PlayPause", "toggleplay": "PlayPause",
                  "stop": "Stop", "next": "Next",
                  "previous": "Previous"}
        try:
            if w in simple:
                mp.call(name, simple[w])
            elif w == "setposition":
                meta = mp.prop(name, "Metadata") or {}
                length = float(meta.get("mpris:length", 0) or 0)
                track = meta.get("mpris:trackid")
                pct = parse_number(arg.lstrip("+"), 0.0)
                cur = mp.position_us(name)
                target = cur + length * pct / 100 if arg.strip().startswith(("+", "-")) else length * pct / 100
                if track:
                    mp.call(name, "SetPosition", "(ox)", (track, int(max(0, min(length, target)))))
            elif w == "setvolume":
                cur = float(mp.prop(name, "Volume") or 0.0) * 100
                v = parse_number(arg.lstrip("+"), 0.0)
                new = cur + v if arg.strip().startswith(("+", "-")) else v
                mp.set_prop(name, "Volume", mp.GLib.Variant("d", max(0.0, min(100.0, new)) / 100.0))
            elif w in ("setshuffle", "toggleshuffle"):
                v = -1 if w == "toggleshuffle" else int(parse_number(arg, -1))
                cur = bool(mp.prop(name, "Shuffle"))
                mp.set_prop(name, "Shuffle", mp.GLib.Variant("b", (not cur) if v == -1 else bool(v)))
            elif w in ("setrepeat", "togglerepeat"):
                v = -1 if w == "togglerepeat" else int(parse_number(arg, -1))
                cur = (mp.prop(name, "LoopStatus") or "None") != "None"
                on = (not cur) if v == -1 else bool(v)
                mp.set_prop(name, "LoopStatus", mp.GLib.Variant("s", "Playlist" if on else "None"))
            elif w == "closeplayer":
                mp.quit(name)
            elif w in ("openplayer", "toggleplayer"):
                self._open_player()
            else:
                super().command(args)
        except Exception:
            log.exception("[%s] NowPlaying %s failed", self.name, args)

    def _open_player(self) -> None:
        pref = self.player_pref.lower() or "spotify"
        exe = shutil.which(pref)
        if exe:
            subprocess.Popen([exe], start_new_session=True)


# --------------------------------------------------------------------------- volume
def _run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False).stdout


def get_volume() -> tuple[float, bool, str]:
    """(volume 0..100, muted, device description) of the default output."""
    if shutil.which("wpctl"):
        out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"([\d.]+)", out)
        vol = float(m.group(1)) * 100 if m else 0.0
        desc = ""
        insp = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
        dm = re.search(r'node\.description = "([^"]*)"', insp)
        if dm:
            desc = dm.group(1)
        return vol, "MUTED" in out, desc
    if shutil.which("pactl"):
        out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", out)
        mute = "yes" in _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        return float(m.group(1)) if m else 0.0, mute, _run(["pactl", "get-default-sink"]).strip()
    return 0.0, False, ""


def set_volume(percent: float | None = None, delta: float | None = None, mute: str | None = None) -> None:
    if shutil.which("wpctl"):
        if percent is not None:
            subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0.0, percent) / 100:.2f}"], check=False)
        if delta is not None:
            sign = "+" if delta >= 0 else "-"
            subprocess.run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{abs(delta) / 100:.2f}{sign}"], check=False)
        if mute is not None:
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", mute], check=False)
    elif shutil.which("pactl"):
        if percent is not None:
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{int(max(0.0, percent))}%"], check=False)
        if delta is not None:
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{'+' if delta >= 0 else '-'}{int(abs(delta))}%"], check=False)
        if mute is not None:
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", {"toggle": "toggle", "1": "1", "0": "0"}[mute]], check=False)


class AudioPlugin(Measure):
    """``Plugin=Win7AudioPlugin``: default output volume (-1 when muted), string = device name."""

    bang_namespace = "audio"

    def read_measure(self) -> None:
        if not self.has("maxvalue"):
            self.max_value = 100.0
        if not hasattr(self, "_busy"):
            self._busy = False

    def update_value(self) -> None:
        if self._busy:
            return
        self._busy = True

        def done(result: Any) -> None:
            self._busy = False
            if isinstance(result, Exception):
                return
            vol, muted, desc = result
            self._raw_number = -1.0 if muted else vol
            self.value = self._raw_number
            self.string = desc

        self.run_in_background(get_volume, done)

    def command(self, args: str) -> None:
        word, _sep, arg = args.strip().partition(" ")
        w = word.lower()
        if w == "setvolume":
            set_volume(percent=parse_number(arg, 0.0))
        elif w == "changevolume":
            set_volume(delta=parse_number(arg, 0.0))
        elif w in ("togglemute", "mute", "unmute"):
            set_volume(mute={"togglemute": "toggle", "mute": "1", "unmute": "0"}[w])
        else:
            super().command(args)


class MediaKeyPlugin(Measure):
    """Media keys: NextTrack, PrevTrack, Stop, PlayPause, VolumeMute, VolumeDown, VolumeUp."""

    def command(self, args: str) -> None:
        w = args.strip().lower()
        mp = Mpris.get()
        name = mp.pick("") if mp else None
        actions = {"nexttrack": "Next", "prevtrack": "Previous", "stop": "Stop", "playpause": "PlayPause"}
        if w in actions and mp and name:
            mp.call(name, actions[w])
        elif w == "volumemute":
            set_volume(mute="toggle")
        elif w in ("volumedown", "volumeup"):
            set_volume(delta=-2 if w == "volumedown" else 2)
        else:
            super().command(args)


# --------------------------------------------------------------------------- AudioLevel
class _AudioCapture:
    """Reads the default output monitor (PipeWire/PulseAudio) and computes levels and bands."""

    RATE = 44100
    CHUNK = 1024

    def __init__(self, port: str, fft_size: int, bands: int, fmin: float, fmax: float) -> None:
        self.port = port
        self.fft_size = fft_size
        self.bands = bands
        self.fmin, self.fmax = fmin, fmax
        self.rms = [0.0, 0.0]
        self.peak = [0.0, 0.0]
        self.band_values: list[float] = [0.0] * bands
        self.fft_values: list[float] = []
        self.status = 0
        self.proc: subprocess.Popen[bytes] | None = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="raintux-audio", daemon=True)
        self.thread.start()

    def _argv(self) -> list[str] | None:
        if shutil.which("parec"):
            dev = "@DEFAULT_MONITOR@" if self.port != "input" else "@DEFAULT_SOURCE@"
            return ["parec", "--device", dev, "--format=s16le", f"--rate={self.RATE}", "--channels=2", "--latency-msec=20"]
        if shutil.which("pw-record"):
            argv = ["pw-record", "--format", "s16", "--rate", str(self.RATE), "--channels", "2"]
            if self.port != "input":
                argv += ["-P", "{ stream.capture.sink=true }"]
            return argv + ["-"]
        return None

    def _run(self) -> None:
        argv = self._argv()
        if argv is None:
            log.info("AudioLevel: neither parec nor pw-record found")
            return
        try:
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return
        self.status = 1
        try:
            import numpy as np
        except ImportError:
            np = None  # type: ignore[assignment]
        buf = []
        while not self.stop.is_set():
            data = self.proc.stdout.read(self.CHUNK * 4) if self.proc.stdout else b""
            if not data:
                break
            n = len(data) // 4
            if np is not None:
                frames = np.frombuffer(data[: n * 4], dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768.0
                self.rms = [float(np.sqrt(np.mean(frames[:, c] ** 2))) for c in (0, 1)]
                self.peak = [float(np.max(np.abs(frames[:, c]))) for c in (0, 1)]
                if self.fft_size:
                    buf.append(frames.mean(axis=1))
                    joined = np.concatenate(buf)
                    if len(joined) >= self.fft_size:
                        seg = joined[-self.fft_size:] * np.hanning(self.fft_size)
                        mag = np.abs(np.fft.rfft(seg)) / (self.fft_size / 2)
                        self.fft_values = mag.tolist()
                        self._bands(mag, np)
                        buf = [joined[-self.fft_size // 2:]]
            else:
                samples = struct.unpack(f"<{n * 2}h", data[: n * 4])
                for c in (0, 1):
                    ch = samples[c::2]
                    self.rms[c] = math.sqrt(sum(s * s for s in ch) / max(1, len(ch))) / 32768.0
                    self.peak[c] = max((abs(s) for s in ch), default=0) / 32768.0
        self.status = 0

    def _bands(self, mag: Any, np: Any) -> None:
        if self.bands <= 0:
            return
        freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.RATE)
        edges = np.geomspace(max(1.0, self.fmin), self.fmax, self.bands + 1)
        vals = []
        for i in range(self.bands):
            sel = mag[(freqs >= edges[i]) & (freqs < edges[i + 1])]
            vals.append(float(sel.max()) if sel.size else 0.0)
        self.band_values = vals

    def close(self) -> None:
        self.stop.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()


class AudioLevelPlugin(Measure):
    """Parent: ``Port``, ``FFTSize``, ``Bands``, ``FreqMin``/``FreqMax``, ``Sensitivity``, attack/decay.
    Child: ``Parent=``, ``Type=RMS|Peak|Band|FFT|DeviceStatus``, ``Channel``, ``BandIdx``/``FFTIdx``."""

    def read_measure(self) -> None:
        self.parent_name = self.get("parent").strip()
        self.kind = self.get("type", "RMS").strip().lower()
        self.channel = self.get("channel", "Avg").strip().lower()
        self.band_idx = self.get_int("bandidx", 0)
        self.fft_idx = self.get_int("fftidx", 0)
        if not hasattr(self, "_cap"):
            self._cap: _AudioCapture | None = None
            self._smooth = 0.0
        self.sensitivity = self.get_number("sensitivity", 35.0)
        self.rms_gain = self.get_number("rmsgain", 1.0)
        self.peak_gain = self.get_number("peakgain", 1.0)
        self.attack = self.get_number("rmsattack" if self.kind == "rms" else "fftattack", 300)
        self.decay = self.get_number("rmsdecay" if self.kind == "rms" else "fftdecay", 300)

    def _root(self) -> AudioLevelPlugin:
        if self.parent_name:
            m = self.skin.measure(self.parent_name)
            if isinstance(m, AudioLevelPlugin):
                return m
        return self

    def initialize(self) -> None:
        if self.parent_name:
            return
        self._cap = _AudioCapture(
            self.get("port", "Output").strip().lower(),
            self.get_int("fftsize", 0),
            self.get_int("bands", 0),
            self.get_number("freqmin", 20.0),
            self.get_number("freqmax", 20000.0),
        )

    def _channel(self, pair: list[float]) -> float:
        if self.channel in ("l", "left", "0"):
            return pair[0]
        if self.channel in ("r", "right", "1"):
            return pair[1]
        if self.channel == "sum":
            return pair[0] + pair[1]
        return (pair[0] + pair[1]) / 2

    def update_value(self) -> None:
        cap = self._root()._cap
        if cap is None:
            self._raw_number = 0.0
            return
        k = self.kind
        if k == "devicestatus":
            self._raw_number = float(cap.status)
            return
        if k == "rms":
            raw = min(1.0, self._channel(cap.rms) * self.rms_gain)
        elif k == "peak":
            raw = min(1.0, self._channel(cap.peak) * self.peak_gain)
        elif k == "band":
            vals = cap.band_values
            raw = self._db(vals[self.band_idx]) if self.band_idx < len(vals) else 0.0
        elif k == "fft":
            vals = cap.fft_values
            raw = self._db(vals[self.fft_idx]) if self.fft_idx < len(vals) else 0.0
        else:
            raw = 0.0
        # Simple attack/decay smoothing per skin update.
        rate = self.attack if raw > self._smooth else self.decay
        k_s = 1.0 if rate <= 0 else min(1.0, self.skin.update_ms / max(1.0, rate))
        self._smooth += (raw - self._smooth) * k_s
        self._raw_number = self._smooth

    def _db(self, mag: float) -> float:
        if mag <= 0:
            return 0.0
        db = 20 * math.log10(mag)
        return max(0.0, min(1.0, 1.0 + db / max(1.0, self.sensitivity)))

    def finalize(self) -> None:
        if getattr(self, "_cap", None) is not None:
            self._cap.close()
