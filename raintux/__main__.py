"""CLI entry: `python -m raintux` or `raintux`."""

from __future__ import annotations

import os
import sys

USAGE = """\
usage: raintux [command]

With no arguments, start RainTux (or open Manage if it is already running).

  raintux --manage [Config]        open the Manage window
  raintux --refresh-all            refresh every loaded skin
  raintux FILE.rmskin              install a Rainmeter skin package
  raintux install FILE.rmskin      same
  raintux '!Bang' args...          run a bang in the running instance, like Rainmeter.exe
                                   e.g.  raintux '!ActivateConfig' 'illustro\\Clock'
  raintux --version | --help

Environment: RAINTUX_SKIP_FIRST_RUN=1, RAINTUX_DEV=1 (debug log), RAINTUX_NO_PRELOAD=1
"""

_ORIG_PRELOAD = "RAINTUX_ORIG_LD_PRELOAD"


def _reexec_with_layer_shell_preload() -> None:
    """
    Re-exec once with ``LD_PRELOAD`` set to gtk4-layer-shell.

    gtk4-layer-shell must be loaded before libwayland-client, which Python's
    GObject bindings can't guarantee; without the preload every skin silently
    falls back to a normal window. See gtk4-layer-shell's ``linking.md``.
    Opt out with ``RAINTUX_NO_PRELOAD=1``.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("RAINTUX_NO_PRELOAD", "").lower() in ("1", "true", "yes"):
        return
    preload = os.environ.get("LD_PRELOAD", "")
    if "gtk4-layer-shell" in preload or _ORIG_PRELOAD in os.environ:
        return
    from ctypes.util import find_library

    lib = find_library("gtk4-layer-shell")
    if not lib:
        return
    env = dict(os.environ)
    env["LD_PRELOAD"] = f"{lib}:{preload}" if preload else lib
    env[_ORIG_PRELOAD] = preload
    os.execve(sys.executable, sys.orig_argv, env)


def _restore_environment() -> None:
    """The library is loaded now; don't pass the preload on to programs skins launch."""
    if _ORIG_PRELOAD not in os.environ:
        return
    orig = os.environ.pop(_ORIG_PRELOAD)
    if orig:
        os.environ["LD_PRELOAD"] = orig
    else:
        os.environ.pop("LD_PRELOAD", None)


def main() -> None:
    """Start RainTux, or pass arguments (``!Bang``, ``--manage``, ``file.rmskin``) to the running instance."""
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    if args and args[0] in ("-V", "--version"):
        from raintux import __version__

        print(f"RainTux {__version__}")
        return
    _reexec_with_layer_shell_preload()
    _restore_environment()
    from raintux.launch_env import load_launch_env

    load_launch_env()
    from raintux.core.engine import run_engine

    dev = os.environ.get("RAINTUX_DEV", "").lower() in ("1", "true", "yes")
    raise SystemExit(run_engine(dev_mode=dev, argv=sys.argv))


if __name__ == "__main__":
    main()
