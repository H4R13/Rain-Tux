"""Windows click targets in skin actions (raintux.compat.shell), from Summit's dock and others."""

import struct
from pathlib import Path

import pytest

from raintux.compat.shell import read_lnk, translate

INSTALLED = {"google-chrome", "firefox", "vlc", "gnome-system-monitor", "libreoffice"}


def which(name: str) -> str | None:
    return f"/usr/bin/{name}" if name in INSTALLED else None


def run(cmd: list[str], tmp_path: Path):
    return translate(cmd, lambda p: tmp_path / p.replace("\\", "/"), which)


def _lnk(path: Path, target: str, arguments: str = "") -> Path:
    """A minimal shell link (MS-SHLLINK): header, LinkInfo with LocalBasePath, unicode arguments."""
    flags = 0x02 | 0x80 | (0x20 if arguments else 0)  # HasLinkInfo, IsUnicode, HasArguments
    header = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02\x00" + b"\x00" * 4 + b"\xc0" + b"\x00" * 6 + b"\x46",
                         flags) + b"\x00" * (76 - 24)
    base = target.encode("latin-1") + b"\0"
    info_header = 28
    info = struct.pack("<IIIIIII", info_header + len(base) + 1, info_header, 1, 0, info_header, 0,
                       info_header + len(base)) + base + b"\0"
    strings = struct.pack("<H", len(arguments)) + arguments.encode("utf-16-le") if arguments else b""
    path.write_bytes(header + info + strings)
    return path


@pytest.mark.parametrize(("cmd", "uri"), [
    (["Explorer", "Shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"], "computer:///"),
    (["shell:RecycleBinFolder"], "trash:///"),
    (["explorer.exe", "::{645FF040-5081-101B-9F08-00AA002F954E}"], "trash:///"),
])
def test_shell_folders(cmd: list[str], uri: str, tmp_path: Path) -> None:
    assert run(cmd, tmp_path).uri == uri


@pytest.mark.parametrize(("cmd", "argv"), [
    (["shutdown.exe", "/s", "/t", "0"], ["systemctl", "poweroff"]),
    (["shutdown", "/r", "/t", "120"], ["shutdown", "-r", "+2"]),
    (["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], ["systemctl", "suspend"]),
    (["rundll32.exe", "user32.dll,LockWorkStation"], ["loginctl", "lock-session"]),
    (["chrome.exe"], ["/usr/bin/google-chrome"]),
    ([r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe", "--fullscreen"], ["/usr/bin/vlc", "--fullscreen"]),
    (["taskmgr.exe"], ["/usr/bin/gnome-system-monitor"]),
    (["winword.exe", "a.docx"], ["/usr/bin/libreoffice", "--writer", "a.docx"]),
])
def test_programs_and_power(cmd: list[str], argv: list[str], tmp_path: Path) -> None:
    assert run(cmd, tmp_path).argv == argv


def test_roles_and_unknown(tmp_path: Path) -> None:
    assert run(["ms-settings:"], tmp_path).role == "@settings"
    assert run(["msedge.exe"], tmp_path).role == "@browser"  # Edge isn't installed: the default browser
    assert run(["cmd.exe"], tmp_path).role == "@terminal"
    assert "no Linux counterpart" in run(["hearthstone.exe"], tmp_path).note
    assert run(["https://example.com"], tmp_path) is None  # not Windows-only: runs as before
    (tmp_path / "notes.txt").write_text("x")
    assert run(["notes.txt"], tmp_path) is None


def test_windows_argv() -> None:
    from raintux.compat.shell import windows_argv

    assert windows_argv('--profile-directory="Profile 1" --app-id=x') == ["--profile-directory=Profile 1", "--app-id=x"]
    assert windows_argv(r'"C:\a b\c.exe" x\y "q\"uote" ""') == [r"C:\a b\c.exe", r"x\y", 'q"uote', ""]


def test_lnk_shortcuts(tmp_path: Path) -> None:
    """Summit's dock opens @Resources\\Application\\Music, a .lnk to Chrome's YouTube Music app."""
    app = tmp_path / "Application"
    app.mkdir()
    lnk = _lnk(app / "Music.lnk", r"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe",
               "--profile-directory=Default --app-id=cinhim")
    assert read_lnk(lnk) == (r"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe",
                             ["--profile-directory=Default", "--app-id=cinhim"])
    resolve = lambda p: tmp_path / p.replace("\\", "/").replace("music", "Music")  # noqa: E731
    action = translate([r"Application\music"], resolve, which)
    assert action.argv == ["/usr/bin/google-chrome", "--profile-directory=Default", "--app-id=cinhim"]
