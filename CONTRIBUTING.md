# Contributing to RainTux

Thanks for helping! RainTux runs Rainmeter skins on Linux, and every fix that makes a real skin
work better is welcome. Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): it explains how
the code is laid out and where each kind of change goes.

## Set up

On a Linux desktop the installer sets up everything (GTK 4, PyGObject, gtk4-layer-shell):

```bash
curl -fsSL https://raw.githubusercontent.com/H4R13/Rain-Tux/main/install.sh | bash
```

For working on the code, use a checkout and a virtual environment that can see the distro's
PyGObject and pycairo:

```bash
git clone https://github.com/H4R13/Rain-Tux.git && cd Rain-Tux
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
RAINTUX_DEV=1 .venv/bin/python -m raintux
```

## Run the tests

```bash
.venv/bin/python -m pytest
```

The tests need no display. Tests marked `gtk` measure text with Pango and are skipped where GTK
isn't installed; Lua tests skip without `lupa`. `tests/helpers.py` `make_skin()` writes a small
skin to a temporary folder and loads it with `NullHost`, which records commands, bangs and
timers instead of touching the desktop:

```python
from helpers import make_skin

def test_my_fix(tmp_path):
    skin, host = make_skin(tmp_path, """
        [MeasureCalc]
        Measure=Calc
        Formula=6*7
    """)
    skin.update()
    assert skin.measure("MeasureCalc").value == 42
```

### Working from Windows

RainTux needs Linux, but WSL works well (Ubuntu 24.04). Keep the checkout on Windows if you like,
copy it into WSL to run the tests, and run them from that copy:

```bash
rsync -a --delete --exclude .git /mnt/c/path/to/Rain-Tux/ ~/raintux-test/
cd ~/raintux-test && ~/venv/bin/python -m pytest
```

On Windows 11, WSLg shows Linux windows on your desktop, so you can also run RainTux inside a
nested Sway session (`sway` started from WSL opens as a window) to see skins live.

## Making a change

- **Rainmeter decides.** When RainTux behaves differently from Rainmeter, Rainmeter is right.
  Find the code in Rainmeter's source (https://github.com/rainmeter/rainmeter, mostly
  `Library/`) that handles the option or bang, match it, and name the file in your commit message.
- **Add a test** that fails without your change. If a real skin exposed the bug, name the test
  after what the skin does and mention the skin in a comment (see `tests/test_real_skins.py`).
- **Keep the core GTK-free.** `raintux/core` (except `engine.py`), `raintux/measures` and
  `raintux/meters` must import without GTK, so they stay testable anywhere. Desktop work goes
  through `SkinHost`.
- **Match the code around you**: type hints, a one-line docstring that says what the thing does,
  short comments only where the reason isn't obvious. No new dependencies without asking first.
- **Small commits** with messages that say what now works, e.g. "Image meters take clicks over
  their whole rectangle, like Rainmeter".

## Skins in this repository

Only skins whose license allows redistribution go into `raintux/data/skins`, each with its
license file and credits (see `illustro/LICENSE.txt`). Never commit third-party skin packages
for testing; download them in your test setup instead.

## License of contributions

RainTux is licensed under the GNU GPL, version 2 or later (see [LICENSE](LICENSE) and
[NOTICE](NOTICE)). By sending a contribution you agree that it is licensed the same way.
Bundled skins keep their own licenses, listed in NOTICE.
