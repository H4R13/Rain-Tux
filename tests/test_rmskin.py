import zipfile
from pathlib import Path

import pytest

from raintux.core.config import default_skins_roots
from raintux.core.rmskin import install_package, install_root, open_package


def _package(path: Path, files: dict[str, str], footer: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    if footer:  # .rmskin files end with a size + "RMSKIN" footer after the zip data
        with open(path, "ab") as f:
            f.write((1234).to_bytes(8, "little") + b"RMSKIN\x00")
    return path


RMSKIN_INI = """[rmskin]
Name=Suite
Author=Someone
Version=2.0
LoadType=Skin
Load=Suite\\Clock\\Clock.ini
VariableFiles=Suite\\@Resources\\Vars.inc
"""


def test_open_package_reads_metadata(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "s.rmskin", {
        "RMSKIN.ini": RMSKIN_INI,
        "Skins/Suite/Clock/Clock.ini": "[Rainmeter]\n",
        "Plugins/64bit/Thing.dll": "MZ",
        "Skins/Suite/@Resources/Fonts/A.ttf": "font",
    })
    info, zf = open_package(pkg)
    zf.close()
    assert (info.name, info.author, info.version) == ("Suite", "Someone", "2.0")
    assert info.load == "Suite\\Clock\\Clock.ini"
    assert info.skins == ["Suite"]
    assert info.plugins == ["Thing.dll"]
    assert info.fonts == ["A.ttf"]


def test_install_backs_up_and_keeps_variables(tmp_path: Path) -> None:
    root = tmp_path / "Skins"
    old = root / "Suite" / "@Resources"
    old.mkdir(parents=True)
    (old / "Vars.inc").write_text("[Variables]\nColor=1,1,1\nUnchanged=a\n")
    pkg = _package(tmp_path / "s.rmskin", {
        "RMSKIN.ini": RMSKIN_INI,
        "Skins/Suite/Clock/Clock.ini": "[Rainmeter]\nUpdate=1000\n",
        "Skins/Suite/@Resources/Vars.inc": "[Variables]\nColor=9,9,9\nUnchanged=a\nNew=x\n",
    })
    info = install_package(pkg, root)
    assert (root / "Suite" / "Clock" / "Clock.ini").is_file()
    vars_text = (root / "Suite" / "@Resources" / "Vars.inc").read_text()
    assert "Color=1,1,1" in vars_text and "New=x" in vars_text  # user value kept, new key added
    backups = list((root / "@Backup").iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("Suite-")
    assert info.skins == ["Suite"]
    assert not any(p.name.startswith(".install-") for p in root.iterdir())


@pytest.mark.parametrize("evil", ["Skins/../../escape.ini", "Skins/Suite/../../../escape.ini", "/abs.ini"])
def test_zip_slip_is_ignored(tmp_path: Path, evil: str) -> None:
    pkg = _package(tmp_path / "s.rmskin", {"RMSKIN.ini": RMSKIN_INI, "Skins/Suite/ok.ini": "x", evil: "pwned"})
    install_package(pkg, tmp_path / "Skins")
    assert not (tmp_path / "escape.ini").exists()
    assert not Path("/abs.ini").exists()
    assert (tmp_path / "Skins" / "Suite" / "ok.ini").is_file()


@pytest.mark.parametrize(("script", "python"), [(r"#@#Scripts\Main.py", True), ('"main.PY"', True),
                                                ("Main.lua", False), (None, False)])
def test_python_skins_install_into_their_own_folder(tmp_path: Path, script: str | None, python: bool) -> None:
    ini = "[Rainmeter]\n[MeasureScript]\nMeasure=Script\n" + (f"ScriptFile={script}\n" if script else "")
    pkg = _package(tmp_path / "s.rmskin", {"RMSKIN.ini": RMSKIN_INI, "Skins/Suite/Clock/Clock.ini": ini})
    info, zf = open_package(pkg)
    zf.close()
    assert info.python_scripts is python
    skins, py_skins = tmp_path / "Skins", tmp_path / "PythonSkins"
    root = install_root(info, skins, py_skins)
    assert root == (py_skins if python else skins)
    install_package(pkg, root)
    assert (root / "Suite" / "Clock" / "Clock.ini").is_file()


def test_lua_and_python_skins_have_separate_default_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    roots = default_skins_roots()
    assert roots[:2] == [tmp_path / "RainTux" / "Skins", tmp_path / "RainTux" / "PythonSkins"]
