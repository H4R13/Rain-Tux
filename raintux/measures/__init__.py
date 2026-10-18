"""Rainmeter-style measures (data sources) and the ``Measure=`` / ``Plugin=`` registry."""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from raintux.measures.base_measure import Measure

if TYPE_CHECKING:
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)

# Measure= value (lower-case) -> "module:Class"
_BUILTIN: dict[str, str] = {
    "cpu": "cpu_measure:CpuMeasure",
    "physicalmemory": "ram_measure:RamMeasure",
    "memory": "ram_measure:RamMeasure",
    "swapmemory": "ram_measure:RamMeasure",
    "swap": "ram_measure:RamMeasure",
    "freediskspace": "disk_measure:DiskMeasure",
    "freedisk": "disk_measure:DiskMeasure",
    "disk": "disk_measure:DiskMeasure",
    "netin": "network_measure:NetworkMeasure",
    "netout": "network_measure:NetworkMeasure",
    "nettotal": "network_measure:NetworkMeasure",
    "time": "time_measure:TimeMeasure",
    "calc": "calc_measure:CalcMeasure",
    "string": "misc_measures:StringMeasure",
    "uptime": "misc_measures:UptimeMeasure",
    "loop": "misc_measures:LoopMeasure",
    "script": "script_measure:ScriptMeasure",
    "registry": "misc_measures:RegistryMeasure",
    # Current Rainmeter builds the old default plugins in as measure types.
    "webparser": "plugins.webparser:WebParserPlugin",
    "actiontimer": "plugins.actiontimer:ActionTimerPlugin",
    "advancedcpu": "plugins.system:UsageMonitorPlugin",
    "audio": "plugins.media:AudioPlugin",
    "audiolevel": "plugins.media:AudioLevelPlugin",
    "coretemp": "plugins.system:TemperaturePlugin",
    "speedfan": "plugins.system:TemperaturePlugin",
    "dragdrop": "plugins.dragdrop:DragDropMeasure",
    "fileview": "plugins.files:FileViewPlugin",
    "folderinfo": "plugins.files:FolderInfoPlugin",
    "inputtext": "plugins.inputtext:InputTextPlugin",
    "mediakey": "plugins.media:MediaKeyPlugin",
    "mouse": "plugins.mouse:MouseMeasure",
    "nowplaying": "plugins.media:NowPlayingPlugin",
    "ping": "plugins.system:PingPlugin",
    "power": "plugins.system:PowerPlugin",
    "process": "plugins.system:ProcessPlugin",
    "quote": "plugins.files:QuotePlugin",
    "recyclemanager": "plugins.files:RecycleManagerPlugin",
    "runcommand": "plugins.system:RunCommandPlugin",
    "sysinfo": "plugins.system:SysInfoPlugin",
    "usagemonitor": "plugins.system:UsageMonitorPlugin",
    "wifistatus": "plugins.system:WiFiStatusPlugin",
    "perfmon": "misc_measures:UnsupportedMeasure",
    "resmon": "misc_measures:UnsupportedMeasure",
    "itunes": "plugins.media:NowPlayingPlugin",
    "windowmessage": "misc_measures:UnsupportedMeasure",
}

# Plugin= name (lower-case, without path / ".dll") -> "module:Class" under raintux.measures.plugins
_PLUGINS: dict[str, str] = {
    "actiontimer": "actiontimer:ActionTimerPlugin",
    "webparser": "webparser:WebParserPlugin",
    "powerplugin": "system:PowerPlugin",
    "sysinfo": "system:SysInfoPlugin",
    "process": "system:ProcessPlugin",
    "folderinfo": "files:FolderInfoPlugin",
    "quoteplugin": "files:QuotePlugin",
    "recyclemanager": "files:RecycleManagerPlugin",
    "fileview": "files:FileViewPlugin",
    "runcommand": "system:RunCommandPlugin",
    "pingplugin": "system:PingPlugin",
    "nowplaying": "media:NowPlayingPlugin",
    "win7audioplugin": "media:AudioPlugin",
    "audiolevel": "media:AudioLevelPlugin",
    "mediakey": "media:MediaKeyPlugin",
    "usagemonitor": "system:UsageMonitorPlugin",
    "advancedcpu": "system:UsageMonitorPlugin",
    "wifistatus": "system:WiFiStatusPlugin",
    "coretemp": "system:TemperaturePlugin",
    "speedfanplugin": "system:TemperaturePlugin",
    "inputtext": "inputtext:InputTextPlugin",
    "drag&drop": "dragdrop:DragDropMeasure",
    "mouse": "mouse:MouseMeasure",
    "itunesplugin": "media:NowPlayingPlugin",
    # Windows player plugins: the MPRIS player instead.
    "webnowplaying": "media:WebNowPlayingPlugin",
    "gpmdpplugin": "media:GpmdpPlugin",
    "spotifyplugin": "media:SpotifyPlugin",
    "perfmon": "",
    "resmon": "",
    "windowmessageplugin": "",
}


def _load(module_path: str) -> type[Measure] | None:
    mod_name, cls_name = module_path.split(":")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError:
        log.exception("cannot import %s", mod_name)
        return None
    return getattr(mod, cls_name, None)


def plugin_key(name: str) -> str:
    """``Plugins\\WebParser.dll`` → ``webparser``."""
    n = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return n[:-4] if n.endswith(".dll") else n


def create_measure(skin: Skin, name: str, raw: dict[str, str]) -> Measure:
    """Instantiate the measure class for a section (unknown types become constant 0)."""
    from raintux.measures.misc_measures import UnsupportedMeasure

    opts = {k.lower(): v for k, v in raw.items()}
    mtype = skin.resolve(opts.get("measure") or opts.get("type") or "").strip().lower()
    target: str | None
    if mtype == "plugin":
        pkey = plugin_key(skin.resolve(opts.get("plugin", "")))
        target = _PLUGINS.get(pkey)
        if target:
            target = "raintux.measures.plugins." + target
        else:
            log.warning("[%s] plugin %r is not available on Linux", name, opts.get("plugin"))
    else:
        target = _BUILTIN.get(mtype)
        if target:
            target = "raintux.measures." + target
        else:
            log.warning("[%s] unknown measure type %r", name, mtype)
    cls = _load(target) if target else None
    return (cls or UnsupportedMeasure)(skin, name, raw)
