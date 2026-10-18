"""Load real Rainmeter suites in RainTux, screenshot them and list what RainTux logged.

Run it through ``tools/suitecheck.sh``, which starts a headless Sway first. For every suite in the
manifest (``tools/popular-skins.json``) it:

1. makes a fresh HOME and installs the suite there (the .rmskin, or a copied skin folder),
2. starts RainTux from this checkout in its own D-Bus session, with the API on ``--port``,
3. loads the package's own ``Load`` (a layout or a skin), or the manifest's ``configs``/``layout``,
4. waits, screenshots the desktop with grim and asks the API which skins are loaded,
5. stops RainTux and sorts its log into errors (tracebacks, ERROR, GTK criticals) and warnings.

The results go to ``--out``: one PNG and one log per suite, plus ``summary.md``/``summary.json``.
The exit status is 1 when any suite logged an error.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Log lines that come from the test machine, not from RainTux or the skins.
NOISE = re.compile(r"libEGL|MESA|DRI3|dri2|Failed to open display|xkbcomp|AT-SPI|Gdk-Message.*Error 71|"
                   r"GLib-GIO-WARNING.*dbus.*Mapping|Unable to acquire session bus|"
                   r"Gtk-WARNING.*Theme parsing error|gtk4-layer-shell.*layer_surface")
ERROR = re.compile(r"^(ERROR|CRITICAL) |Traceback \(most recent call last\)|-CRITICAL \*\*|Segmentation fault")
WARNING = re.compile(r"^WARNING |-WARNING \*\*")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "suite"


class Api:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path: str) -> object:
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.load(r)

    def bang(self, text: str) -> None:
        req = urllib.request.Request(self.base + "/bang", data=json.dumps({"bang": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=30).read()

    def active(self) -> list[str]:
        return [s["id"] for s in self.get("/skins/active")]  # type: ignore[union-attr]

    def wait_up(self, proc: subprocess.Popen, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if proc.poll() is not None:
                return False
            try:
                self.active()
                return True
            except OSError:
                time.sleep(0.5)
        return False


def q(s: str) -> str:
    return '"' + s + '"'


def install(suite: dict, corpus: Path, home: Path) -> dict:
    """Install the suite into ``home``; returns the package's Load / LoadType (empty for folders)."""
    from raintux.core.rmskin import install_package

    skins, layouts = home / "RainTux" / "Skins", home / "RainTux" / "Layouts"
    skins.mkdir(parents=True, exist_ok=True)
    layouts.mkdir(parents=True, exist_ok=True)
    if "folder" in suite:
        src = Path(suite["folder"])
        src = src if src.is_absolute() else corpus / src
        if not (src / "Skins").is_dir():  # one skin folder
            shutil.copytree(src, skins / src.name)
            return {"load": "", "load_type": ""}
        # A source tree laid out like a package: Skins/<suite>, Layouts|Themes/<name>/Rainmeter.ini|.thm
        for d in (src / "Skins").iterdir():
            shutil.copytree(d, skins / d.name)
        for kind in ("Layouts", "Themes"):
            for d in (src / kind).glob("*") if (src / kind).is_dir() else ():
                ini = next((f for f in d.iterdir() if f.name.lower() in ("rainmeter.ini", "rainmeter.thm")), None)
                if ini is not None:
                    (layouts / d.name).mkdir(parents=True, exist_ok=True)
                    shutil.copy(ini, layouts / d.name / "Rainmeter.ini")
        return {"load": "", "load_type": ""}
    found = sorted(glob.glob(str(corpus / suite["package"])))
    if not found:
        raise FileNotFoundError(f"no package matches {corpus / suite['package']}")
    info = install_package(Path(found[-1]), skins, layouts)
    return {"load": info.load, "load_type": info.load_type, "package": Path(found[-1]).name,
            "plugins": sorted(set(info.plugins))}


def prepare_home(home: Path, port: int) -> None:
    if home.exists():
        shutil.rmtree(home)
    cfg = home / ".config" / "raintux"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(
        f'[api]\nenabled = true\nhost = "127.0.0.1"\nport = {port}\n\n[engine]\ndefault_update_ms = 1000\n'
        f'skins_roots = [\n  "{home}/RainTux/Skins",\n]\n\n[ui]\ntray = false\neditor = ""\n', encoding="utf-8")
    # Not RainTux's first launch: don't put its own Welcome / Clock skins on the desktop.
    (cfg / "skins.json").write_text('{"first_run_done": true, "configs": {}}', encoding="utf-8")


def load(api: Api, suite: dict, pkg: dict) -> list[str]:
    """Load what the manifest or the package says; returns the bangs RainTux refused."""
    failed: list[str] = []

    def bang(text: str) -> None:
        try:
            api.bang(text)
        except OSError as e:
            failed.append(f"{text}: {e}")

    configs = suite.get("configs")
    if configs:
        for cfg, ini, *_xy in configs:
            bang(f"!ActivateConfig {q(cfg)} {q(ini)}")
        time.sleep(3)
        for cfg, _ini, *xy in configs:
            if len(xy) == 2:
                bang(f"!Move {xy[0]} {xy[1]} {q(cfg)}")
    elif suite.get("layout") or pkg.get("load_type", "").lower() == "layout":
        bang(f"!LoadLayout {q(suite.get('layout') or pkg['load'])}")
    elif pkg.get("load"):
        config, _, ini = pkg["load"].rpartition("\\")
        bang(f"!ActivateConfig {q(config)} {q(ini)}")
    for b in suite.get("bangs", []):
        bang(b)
    return failed


def classify(log_text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """(errors, warnings) as (line, count), in first-seen order. A traceback counts once, by its last line."""
    errors: dict[str, int] = {}
    warnings: dict[str, int] = {}
    lines = log_text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        i += 1
        if not ln.strip() or NOISE.search(ln):
            continue
        if ln.startswith("Traceback (most recent call last)"):
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                i += 1
            ln = "Traceback: " + (lines[i].strip() if i < len(lines) else "?")
            i += 1
            errors[ln] = errors.get(ln, 0) + 1
        elif ERROR.search(ln):
            errors[ln] = errors.get(ln, 0) + 1
        elif WARNING.search(ln):
            warnings[ln] = warnings.get(ln, 0) + 1
    return list(errors.items()), list(warnings.items())


def run_suite(suite: dict, args: argparse.Namespace) -> dict:
    name = suite["name"]
    work = Path(args.work) / slug(name)
    home = work / "home"
    prepare_home(home, args.port)
    result: dict = {"name": name, "slug": slug(name)}
    try:
        result.update(install(suite, Path(args.corpus), home))
    except Exception as e:  # missing download, broken zip
        result["install_error"] = f"{type(e).__name__}: {e}"
        return result
    log_path = Path(args.out) / f"{slug(name)}.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_CONFIG", "XDG_DATA", "XDG_CACHE", "DBUS_"))}
    env.update(HOME=str(home), RAINTUX_SKIP_FIRST_RUN="1", PYTHONUNBUFFERED="1", NO_AT_BRIDGE="1")
    if args.dev:
        env["RAINTUX_DEV"] = "1"
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(["dbus-run-session", "--", sys.executable, "-m", "raintux"], cwd=str(REPO), env=env,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    api = Api(args.port)
    try:
        if not api.wait_up(proc, 60):
            result["start_error"] = "RainTux did not start (see log)"
        else:
            failed = load(api, suite, result)
            if failed:
                result["bang_errors"] = failed
            time.sleep(float(suite.get("wait", args.wait)))
            shot = Path(args.out) / f"{slug(name)}.png"
            subprocess.run(["grim", str(shot)], check=False)
            result["screenshot"] = shot.name
            try:
                result["active"] = api.active()
            except OSError as e:
                result["active_error"] = str(e)
            if proc.poll() is not None:
                result["crashed"] = proc.returncode
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    errors, warnings = classify(log_path.read_text(encoding="utf-8", errors="replace"))
    result["errors"], result["warnings"] = errors, warnings
    return result


def summarise(results: list[dict], out: Path) -> str:
    rows = ["# RainTux suite check", "", "| Suite | Loaded | Errors | Warnings | Screenshot |", "|---|---|---|---|---|"]
    for r in results:
        problem = r.get("install_error") or r.get("start_error") or (f"crashed ({r['crashed']})" if "crashed" in r else "")
        loaded = problem or str(len(r.get("active", [])))
        rows.append(f"| {r['name']} | {loaded} | {sum(c for _l, c in r.get('errors', []))} | "
                    f"{sum(c for _l, c in r.get('warnings', []))} | {r.get('screenshot', '')} |")
    for r in results:
        rows += ["", f"## {r['name']}", ""]
        if r.get("plugins"):
            rows.append("Windows plugins in the package: " + ", ".join(r["plugins"]))
        if r.get("active"):
            rows.append("Loaded: " + ", ".join(r["active"]))
        if r.get("bang_errors"):
            rows += ["", "Bangs that failed:", "```", *r["bang_errors"], "```"]
        for title, items in (("Errors", r.get("errors", [])), ("Warnings", r.get("warnings", []))):
            if items:
                rows += ["", f"{title}:", "```"]
                rows += [ln + (f"  (x{c})" if c > 1 else "") for ln, c in items[:40]]
                if len(items) > 40:
                    rows.append(f"... {len(items) - 40} more")
                rows.append("```")
    text = "\n".join(rows) + "\n"
    (out / "summary.md").write_text(text, encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(REPO / "tools" / "popular-skins.json"))
    ap.add_argument("--corpus", default=os.environ.get("SUITECHECK_CORPUS", str(Path.home() / "rmskins")),
                    help="folder with one sub-folder of downloads per suite")
    ap.add_argument("--out", default="suitecheck-out")
    ap.add_argument("--work", default="/tmp/suitecheck-homes", help="where the per-suite HOMEs go")
    ap.add_argument("--port", type=int, default=7391, help="RainTux API port (not 7272, to stay off a running RainTux)")
    ap.add_argument("--wait", type=float, default=12, help="seconds between loading and the screenshot")
    ap.add_argument("--dev", action="store_true", help="debug log (RAINTUX_DEV=1)")
    ap.add_argument("--only", nargs="*", default=[], help="suite names or globs, in this order")
    args = ap.parse_args()
    if not os.environ.get("WAYLAND_DISPLAY"):
        print("run this through tools/suitecheck.sh (it starts a headless Sway)", file=sys.stderr)
        return 2
    suites = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["suites"]
    if args.only:
        picked = []
        for pat in args.only:
            picked += [s for s in suites if fnmatch.fnmatch(s["name"].lower(), pat.lower()) and s not in picked]
        suites = picked
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for s in suites:
        print(f"== {s['name']}", flush=True)
        r = run_suite(s, args)
        results.append(r)
        print(f"   loaded {len(r.get('active', []))}, errors {len(r.get('errors', []))}, "
              f"warnings {len(r.get('warnings', []))} {r.get('install_error') or r.get('start_error') or ''}", flush=True)
    summarise(results, out)
    print(f"summary: {out / 'summary.md'}")
    bad = ("errors", "install_error", "start_error", "bang_errors", "crashed")
    return 1 if any(r.get(k) for r in results for k in bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
