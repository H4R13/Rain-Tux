"""CLI entry: `python -m raintux` or `raintux`."""

from __future__ import annotations

import os
import sys


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
    if "gtk4-layer-shell" in preload:
        return
    from ctypes.util import find_library

    lib = find_library("gtk4-layer-shell")
    if not lib:
        return
    env = dict(os.environ)
    env["LD_PRELOAD"] = f"{lib}:{preload}" if preload else lib
    os.execve(sys.executable, sys.orig_argv, env)


def main() -> None:
    """Start RainTux, or pass arguments (``!Bang``, ``--manage``, ``file.rmskin``) to the running instance."""
    _reexec_with_layer_shell_preload()
    from raintux.launch_env import load_launch_env

    load_launch_env()
    # Late import so `--help` can stay fast if we add argparse later.
    from raintux.core.engine import run_engine

    dev = os.environ.get("RAINTUX_DEV", "").lower() in ("1", "true", "yes")
    raise SystemExit(run_engine(dev_mode=dev, argv=sys.argv))


if __name__ == "__main__":
    main()
