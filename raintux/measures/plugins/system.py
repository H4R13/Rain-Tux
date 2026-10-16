"""System plugins: PowerPlugin, SysInfo, Process, RunCommand, PingPlugin, UsageMonitor,
WiFiStatus and CoreTemp/SpeedFan (temperatures)."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from raintux.core.bangs import split_args
from raintux.measures.base_measure import Measure
from raintux.measures.time_measure import rainmeter_strftime

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- PowerPlugin
class PowerPlugin(Measure):
    """``PowerState=ACLine|Status|Status2|Lifetime|Percent|MHz|HZ``."""

    def read_measure(self) -> None:
        self.state_ = self.get("powerstate", "Percent").strip().lower()
        self.fmt = self.get("format", "%H:%M")
        if self.state_ == "percent" and not self.has("maxvalue"):
            self.max_value = 100.0

    def update_value(self) -> None:
        st = self.state_
        if st in ("mhz", "hz"):
            freq = psutil.cpu_freq()
            mhz = freq.current if freq else 0.0
            self._raw_number = mhz if st == "mhz" else mhz * 1e6
            return
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if st == "acline":
            self._raw_number = 1.0 if bat is None or bat.power_plugged else 0.0
        elif st in ("status", "status2"):
            if bat is None:
                self._raw_number = 0.0
            elif bat.power_plugged and bat.percent < 100:
                self._raw_number = 1.0
            elif bat.percent < 5:
                self._raw_number = 2.0
            elif bat.percent < 25:
                self._raw_number = 3.0
            else:
                self._raw_number = 4.0
        elif st == "lifetime":
            secs = bat.secsleft if bat is not None else -1
            if bat is None or secs in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED) or secs < 0:
                self._raw_number = -1.0
                self.string = "Unknown"
            else:
                self._raw_number = float(secs)
                self.string = rainmeter_strftime(self.fmt, datetime(2000, 1, 1) + timedelta(seconds=secs))
        else:  # percent
            self._raw_number = float(bat.percent) if bat is not None else 100.0


# --------------------------------------------------------------------------- SysInfo
def _default_gateway() -> tuple[str, str]:
    """(interface, gateway IP) from /proc/net/route."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                gw = int(f[2], 16)
                return f[0], socket.inet_ntoa(gw.to_bytes(4, "little"))
    except OSError:
        pass
    return "", ""


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


class SysInfoPlugin(Measure):
    """``SysInfoType`` (+ ``SysInfoData`` index for adapters / monitors)."""

    def read_measure(self) -> None:
        self.info = self.get("sysinfotype", "").strip().upper()
        self.data = self.get_int("sysinfodata", 0)

    def _adapters(self) -> list[tuple[str, Any]]:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        out = []
        for name in sorted(addrs):
            if name == "lo":
                continue
            v4 = next((a for a in addrs[name] if a.family == socket.AF_INET), None)
            if v4 is not None and stats.get(name) and stats[name].isup:
                out.append((name, v4))
        return out

    def _adapter(self) -> tuple[str, Any] | None:
        ads = self._adapters()
        if not ads:
            return None
        if self.data <= 0:  # "best" adapter: the one with the default route
            iface, _gw = _default_gateway()
            return next((a for a in ads if a[0] == iface), ads[0])
        return ads[self.data - 1] if self.data - 1 < len(ads) else None

    def update_value(self) -> None:
        t = self.info
        b = self.skin.builtins()
        s: str | None = None
        n = 0.0
        if t == "COMPUTER_NAME" or t == "HOST_NAME":
            s = socket.gethostname()
        elif t == "USER_NAME":
            s = getpass.getuser()
        elif t == "OS_VERSION":
            s = _os_name()
        elif t == "OS_BITS":
            n = 64.0 if platform.machine().endswith("64") else 32.0
        elif t in ("IP_ADDRESS", "NET_MASK", "ADAPTER_DESCRIPTION"):
            a = self._adapter()
            if a is not None:
                s = {"IP_ADDRESS": a[1].address, "NET_MASK": a[1].netmask or "", "ADAPTER_DESCRIPTION": a[0]}[t]
            else:
                s = ""
        elif t == "GATEWAY_ADDRESS":
            s = _default_gateway()[1]
        elif t == "DNS_SERVER":
            try:
                s = next((ln.split()[1] for ln in Path("/etc/resolv.conf").read_text().splitlines()
                          if ln.startswith("nameserver")), "")
            except OSError:
                s = ""
        elif t == "DOMAIN_NAME":
            s = socket.getfqdn().partition(".")[2]
        elif t in ("INTERNET_CONNECTIVITY", "LAN_CONNECTIVITY"):
            n = 1.0 if (_default_gateway()[1] if t == "INTERNET_CONNECTIVITY" else self._adapters()) else -1.0
        elif t == "NUM_MONITORS":
            n = float(len([k for k in b if k.startswith("SCREENAREAWIDTH@")]) or 1)
        elif t in ("SCREEN_WIDTH", "SCREEN_HEIGHT", "WORK_AREA_WIDTH", "WORK_AREA_HEIGHT", "WORK_AREA_TOP", "WORK_AREA_LEFT"):
            key = {"SCREEN_WIDTH": "SCREENAREAWIDTH", "SCREEN_HEIGHT": "SCREENAREAHEIGHT", "WORK_AREA_WIDTH": "WORKAREAWIDTH",
                   "WORK_AREA_HEIGHT": "WORKAREAHEIGHT", "WORK_AREA_TOP": "WORKAREAY", "WORK_AREA_LEFT": "WORKAREAX"}[t]
            n = float(b.get(key, "0"))
        elif t in ("SCREEN_SIZE", "WORK_AREA"):
            pre = "SCREENAREA" if t == "SCREEN_SIZE" else "WORKAREA"
            s = f"{b.get(pre + 'WIDTH', '0')} x {b.get(pre + 'HEIGHT', '0')}"
        elif t == "TIMEZONE_ISDST":
            n = 1.0 if time.localtime().tm_isdst > 0 else 0.0
        elif t == "TIMEZONE_BIAS":
            n = float(time.altzone if time.localtime().tm_isdst > 0 else time.timezone) / 60.0
        elif t in ("TIMEZONE_STANDARD_NAME", "TIMEZONE_DAYLIGHT_NAME"):
            s = time.tzname[0 if t == "TIMEZONE_STANDARD_NAME" else 1]
        elif t == "USER_LOGONTIME":
            n = psutil.boot_time()
        elif t == "IDLE_TIME":
            n = 0.0
        elif t == "PAGESIZE":
            n = float(os.sysconf("SC_PAGE_SIZE"))
        else:
            s = ""
        self.string = s
        self._raw_number = n if s is None else _num(s)


# --------------------------------------------------------------------------- Process
class ProcessPlugin(Measure):
    """``ProcessName=firefox.exe`` → 1 when running, -1 otherwise."""

    def read_measure(self) -> None:
        name = self.get("processname").strip().lower()
        self.proc = name[:-4] if name.endswith(".exe") else name

    def update_value(self) -> None:
        running = False
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if n == self.proc or n.startswith(self.proc[:15]) and len(self.proc) > 15:
                running = True
                break
        self._raw_number = 1.0 if running else -1.0


# --------------------------------------------------------------------------- RunCommand
class RunCommandPlugin(Measure):
    """Run ``Program``/``Parameter`` on ``!CommandMeasure "Run"``; output becomes the string value."""

    bang_namespace = "runcommand"

    def read_measure(self) -> None:
        self.program = self.get("program").strip()
        self.parameter = self.get("parameter")
        self.folder = self.get("startinfolder")
        self.timeout_ms = self.get_int("timeout", -1)
        self.output_file = self.get("outputfile")
        if not hasattr(self, "_proc"):
            self._proc: subprocess.Popen[bytes] | None = None
            self._exit = 0.0

    def _argv(self) -> list[str]:
        prog = self.program.lower()
        param = self.parameter.strip()
        if prog in ("", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            for pre in ("/c ", "/C ", "-Command ", "-command "):
                if param.startswith(pre):
                    param = param[len(pre):]
            if prog.startswith(("powershell", "pwsh")) and shutil.which("pwsh"):
                return ["pwsh", "-NoProfile", "-Command", param]
            return ["/bin/sh", "-c", param]
        return [self.program, *split_args(param)]

    def update_value(self) -> None:
        self._raw_number = self._exit

    def command(self, args: str) -> None:
        cmd = args.strip().lower()
        if cmd == "run":
            self._run()
        elif cmd in ("kill", "close", "terminate"):
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill() if cmd == "kill" else self._proc.terminate()
        else:
            super().command(args)

    def _run(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            log.info("[%s] RunCommand: still running", self.name)
            return
        argv = self._argv()
        cwd = str(self.skin.path(self.folder)) if self.folder else str(self.skin.root)
        timeout = self.timeout_ms / 1000.0 if self.timeout_ms > 0 else None

        def work() -> tuple[int, str]:
            self._proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                out, _ = self._proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                out, _ = self._proc.communicate()
            return self._proc.returncode, out.decode("utf-8", errors="replace")

        def done(result: Any) -> None:
            if isinstance(result, Exception):
                log.warning("[%s] RunCommand failed: %s", self.name, result)
                self.string = ""
                self._exit = -1.0
            else:
                code, out = result
                self._exit = float(code)
                self.string = out.rstrip("\r\n")
                if self.output_file:
                    try:
                        self.skin.path(self.output_file).write_text(out, encoding="utf-8")
                    except OSError:
                        log.warning("[%s] cannot write OutputFile", self.name)
            self.skin.execute(self.get("finishaction"), self)

        self.run_in_background(work, done)

    def finalize(self) -> None:
        if getattr(self, "_proc", None) is not None and self._proc.poll() is None:
            self._proc.kill()


# --------------------------------------------------------------------------- PingPlugin
class PingPlugin(Measure):
    """Round-trip time in ms to ``DestAddress`` every ``UpdateRate`` updates (default 32)."""

    def read_measure(self) -> None:
        self.dest = self.get("destaddress").strip()
        self.rate = max(1, self.get_int("updaterate", 32))
        self.timeout_value = self.get_number("timeoutvalue", 30000)
        self.timeout = max(1, self.get_int("timeout", 30000))
        if not self.has("maxvalue"):
            self.max_value = self.timeout_value
        if not hasattr(self, "_tick"):
            self._tick = 0
            self._busy = False

    def update_value(self) -> None:
        if self._tick % self.rate == 0 and not self._busy and self.dest:
            self._busy = True
            dest, secs = self.dest, max(1, self.timeout // 1000)

            def work() -> float:
                r = subprocess.run(["ping", "-c", "1", "-W", str(secs), dest], capture_output=True, text=True,
                                   timeout=secs + 2, check=False)
                m = re.search(r"time[=<]([\d.]+)\s*ms", r.stdout)
                return float(m.group(1)) if m else self.timeout_value

            def done(result: Any) -> None:
                self._busy = False
                self._raw_number = self.timeout_value if isinstance(result, Exception) else result
                self.value = self._raw_number
                self.skin.execute(self.get("finishaction"), self)

            self.run_in_background(work, done)
        self._tick += 1


# --------------------------------------------------------------------------- UsageMonitor
class _ProcTable:
    """Shared, once-per-second sample of per-process CPU / memory / IO."""

    def __init__(self) -> None:
        self.stamp = 0.0
        self.procs: dict[int, psutil.Process] = {}
        self.rows: list[tuple[str, float, float, float]] = []  # name, cpu%, rss, io/s
        self._io: dict[int, tuple[float, float]] = {}

    def sample(self) -> list[tuple[str, float, float, float]]:
        now = time.monotonic()
        if now - self.stamp < 0.9:
            return self.rows
        ncpu = psutil.cpu_count() or 1
        rows = []
        seen = set()
        for p in psutil.process_iter(["name", "memory_info"]):
            pid = p.pid
            seen.add(pid)
            proc = self.procs.setdefault(pid, p)
            try:
                cpu = proc.cpu_percent(None) / ncpu
                rss = float(p.info["memory_info"].rss) if p.info.get("memory_info") else 0.0
                io = 0.0
                try:
                    c = proc.io_counters()
                    tot = float(c.read_bytes + c.write_bytes)
                    prev = self._io.get(pid)
                    if prev is not None and now > prev[0]:
                        io = max(0.0, (tot - prev[1]) / (now - prev[0]))
                    self._io[pid] = (now, tot)
                except (psutil.AccessDenied, AttributeError):
                    pass
                rows.append((p.info.get("name") or "", cpu, rss, io))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for pid in list(self.procs):
            if pid not in seen:
                self.procs.pop(pid, None)
                self._io.pop(pid, None)
        self.rows = rows
        self.stamp = now
        return rows


_TABLE = _ProcTable()


class UsageMonitorPlugin(Measure):
    """``Alias=CPU|RAM|IO`` (or Category/Counter), ``Index=N`` for the Nth busiest process, ``Name=`` for one."""

    def read_measure(self) -> None:
        alias = self.get("alias").strip().upper()
        counter = self.get("counter").lower()
        category = self.get("category").lower()
        if not alias:
            if "working set" in counter or "memory" in category:
                alias = "RAM"
            elif "io" in counter:
                alias = "IO"
            else:
                alias = "CPU"
        self.alias = alias
        self.index = self.get_int("index", 0)
        self.proc_name = self.get("name").strip().lower()
        self.blacklist = {b.strip().lower() for b in self.get("blacklist").split("|") if b.strip()}
        if alias == "CPU" and not self.has("maxvalue"):
            self.max_value = 100.0

    def update_value(self) -> None:
        col = {"CPU": 1, "RAM": 2, "IO": 3}.get(self.alias, 1)
        rows = [r for r in _TABLE.sample() if r[0].lower() not in self.blacklist and r[0].lower() != "idle"]
        if self.proc_name and self.proc_name != "_total":
            name = self.proc_name[:-4] if self.proc_name.endswith(".exe") else self.proc_name
            vals = [r for r in rows if r[0].lower() == name]
            self._raw_number = sum(r[col] for r in vals)
            self.string = vals[0][0] if vals else ""
            return
        if self.proc_name == "_total":
            self._raw_number = sum(r[col] for r in rows)
            self.string = "_Total"
            return
        # Combine processes with the same name, like Windows performance counters.
        merged: dict[str, float] = {}
        for r in rows:
            merged[r[0]] = merged.get(r[0], 0.0) + r[col]
        ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
        i = self.index - 1 if self.index > 0 else 0
        if i < len(ranked):
            self.string, self._raw_number = ranked[i]
        else:
            self.string, self._raw_number = "", 0.0


# --------------------------------------------------------------------------- WiFiStatus
class WiFiStatusPlugin(Measure):
    """``WiFiInfoType=SSID|QUALITY|ENCRYPTION|AUTH|LIST|PHY|TXRATE|BSSID`` via NetworkManager or /proc."""

    def read_measure(self) -> None:
        self.info = self.get("wifiinfotype", "SSID").strip().upper()
        self.limit = self.get_int("wifilistlimit", 10)
        if self.info == "QUALITY" and not self.has("maxvalue"):
            self.max_value = 100.0
        if not hasattr(self, "_busy"):
            self._busy = False
            self._tick = 0

    def update_value(self) -> None:
        if self._busy or self._tick % 5:
            self._tick += 1
            return
        self._tick += 1
        self._busy = True
        info, limit = self.info, self.limit

        def work() -> tuple[float, str]:
            if shutil.which("nmcli"):
                fields = "ACTIVE,SSID,SIGNAL,SECURITY,CHAN,RATE,BSSID"
                r = subprocess.run(["nmcli", "-t", "-e", "yes", "-f", fields, "dev", "wifi"], capture_output=True,
                                   text=True, timeout=10, check=False)
                nets = [re.split(r"(?<!\\):", ln) for ln in r.stdout.splitlines() if ln]
                nets = [[f.replace("\\:", ":") for f in n] for n in nets if len(n) >= 7]
                if info == "LIST":
                    names = [n[1] for n in nets if n[1]][:limit]
                    return float(len(names)), "\n".join(names)
                act = next((n for n in nets if n[0] == "yes"), None)
                if act is None:
                    return -1.0, ""
                return {
                    "SSID": (1.0, act[1]), "QUALITY": (float(act[2] or 0), act[2]), "ENCRYPTION": (0.0, act[3] or "None"),
                    "AUTH": (0.0, act[3] or "Open"), "PHY": (0.0, f"channel {act[4]}"), "TXRATE": (_num(act[5]), act[5]),
                    "BSSID": (0.0, act[6]),
                }.get(info, (0.0, ""))
            # Fallback: link quality from /proc/net/wireless (percent of 70).
            for line in Path("/proc/net/wireless").read_text().splitlines()[2:]:
                f = line.split()
                if len(f) > 2:
                    q = float(f[2].rstrip("."))
                    return (min(100.0, q / 70 * 100), "") if info == "QUALITY" else (0.0, f[0].rstrip(":"))
            return -1.0, ""

        def done(result: Any) -> None:
            self._busy = False
            if isinstance(result, Exception):
                return
            self._raw_number, s = result
            self.string = s if info != "QUALITY" else None
            self.value = self._raw_number

        self.run_in_background(work, done)


# --------------------------------------------------------------------------- temperatures
class TemperaturePlugin(Measure):
    """CoreTemp (``CoreTempType``, ``CoreTempIndex``) and SpeedFan (``SpeedFanType``, ``SpeedFanNumber``)."""

    def read_measure(self) -> None:
        kind = (self.get("coretemptype") or self.get("speedfantype") or "Temperature").strip().lower()
        self.kind = kind
        self.index = self.get_int("coretempindex", self.get_int("speedfannumber", 0))
        if not self.has("maxvalue"):
            self.max_value = 100.0

    def update_value(self) -> None:
        if self.kind in ("fan",):
            fans = psutil.sensors_fans() if hasattr(psutil, "sensors_fans") else {}
            vals = [f.current for lst in fans.values() for f in lst]
            self._raw_number = float(vals[self.index]) if self.index < len(vals) else 0.0
            return
        if self.kind in ("cpuspeed", "mhz"):
            freq = psutil.cpu_freq(percpu=True) or []
            self._raw_number = float(freq[self.index].current) if self.index < len(freq) else 0.0
            return
        if self.kind == "load":
            per = psutil.cpu_percent(percpu=True)
            self._raw_number = float(per[self.index]) if self.index < len(per) else 0.0
            return
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        chip = next((temps[k] for k in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz") if k in temps), None)
        if chip is None and temps:
            chip = next(iter(temps.values()))
        if not chip:
            self._raw_number = 0.0
            return
        cores = [t for t in chip if t.label.lower().startswith("core")] or chip
        if self.kind in ("maxtemperature", "tjmax"):
            self._raw_number = float(max((t.high or 100.0) for t in chip))
        else:
            self._raw_number = float(cores[self.index].current) if self.index < len(cores) else float(cores[0].current)


def _num(s: str) -> float:
    m = re.match(r"\s*[-+]?\d+(\.\d+)?", s or "")
    return float(m.group(0)) if m else 0.0
