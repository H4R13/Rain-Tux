"""Plugin measures under a headless host."""

import http.server
import threading
from pathlib import Path

import pytest

from helpers import make_skin
from raintux.core.pcre import compile_pcre


def test_pcre_ungreedy_and_named_groups() -> None:
    rx = compile_pcre("(?siU)<b>(.*)</b>.*<i>(.+)</i>")
    assert rx.search("<B>one</b> x <b>two</b> <i>a</i><i>b</i>").groups() == ("one", "a")
    assert compile_pcre("(?<word>\\w+)").match("hi").group("word") == "hi"
    assert compile_pcre("(?U)a.*?b").match("aXbXb").group(0) == "aXbXb"  # lazy becomes greedy


def test_action_timer_waits_and_repeats(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [Variables]
        X=0

        [MeasureAnim]
        Measure=Plugin
        Plugin=ActionTimer
        ActionList1=Start | Wait 50 | Repeat Step, 20, 3 | Done
        Start=[!SetVariable X 10]
        Step=[!SetVariable X (#X#+1)]
        Done=["finished"]
    """)
    skin.execute('[!CommandMeasure MeasureAnim "Execute 1"]')
    assert skin.get_variable("X") == "10"
    assert host.commands == []
    host.run_timers()
    assert skin.get_variable("X") == "13"  # options re-read: #X# is current every repeat
    assert host.commands == ["finished"]


def test_action_timer_stop(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeasureAnim]
        Measure=Plugin
        Plugin=Plugins\\ActionTimer.dll
        ActionList1=Wait 10 | Late
        Late=["late"]
    """)
    skin.execute('[!CommandMeasure MeasureAnim "Execute 1"][!CommandMeasure MeasureAnim "Stop 1"]')
    host.run_timers()
    assert host.commands == []


@pytest.fixture()
def web_page(tmp_path: Path):
    page = tmp_path / "www"
    page.mkdir()
    (page / "index.html").write_text(
        "<html><title>Weather &amp; More</title><b>21</b><i>Sunny</i><img src='http://x/a.png'></html>")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(page), **kw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/index.html"
    srv.shutdown()


def test_webparser_parent_and_children(tmp_path: Path, web_page: str) -> None:
    skin, host = make_skin(tmp_path, f"""
        [MeasureSite]
        Measure=WebParser
        URL={web_page}
        RegExp=(?siU)<title>(.*)</title>.*<b>(.*)</b>.*<i>(.*)</i>
        DecodeCharacterReference=1
        StringIndex=1
        FinishAction=["done"]

        [MeasureTemp]
        Measure=Plugin
        Plugin=WebParser
        URL=[MeasureSite]
        StringIndex=2

        [MeasureSky]
        Measure=Plugin
        Plugin=WebParser
        URL=[MeasureSite]
        StringIndex=3
        RegExp=(?siU)^(.)(.*)$
        StringIndex2=2
    """, start=False)
    skin.start()
    assert host.drain(timeout=5) >= 1
    assert host.commands == ["done"]
    assert skin.measure("MeasureSite").string_value() == "Weather & More"
    skin.update()
    assert skin.measure("MeasureTemp").value == 21
    assert skin.measure("MeasureSky").string_value() == "unny"


def test_webparser_connect_error(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeasureSite]
        Measure=Plugin
        Plugin=WebParser
        URL=http://127.0.0.1:9/nothing
        RegExp=(.*)
        OnConnectErrorAction=["offline"]
        ErrorString=n/a
    """)
    host.drain(timeout=10)
    assert host.commands == ["offline"]
    assert skin.measure("MeasureSite").string_value() == "n/a"


def test_lua_script_api(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, host = make_skin(tmp_path, """
        [Variables]
        Name=world

        [MeasureCPU]
        Measure=Calc
        Formula=42
        MaxValue=84

        [MeasureScript]
        Measure=Script
        ScriptFile=hello.lua
        Greeting=Hello

        [MeterText]
        Meter=String
        Text=[&MeasureScript:Shout('hey')]
        DynamicVariables=1
    """, files={"Test/hello.lua": """
        function Initialize()
          greeting = SELF:GetOption('Greeting')
          count = 0
        end
        function Update()
          count = count + 1
          local cpu = SKIN:GetMeasure('MeasureCPU')
          SKIN:Bang('!SetVariable', 'Rel', cpu:GetRelativeValue())
          if count > 1 then return greeting .. ', ' .. SKIN:GetVariable('Name') end
          return count
        end
        function Shout(s) return string.upper(s) .. '!' end
    """})
    m = skin.measure("MeasureScript")
    assert m.value == 1
    skin.update()
    assert m.string_value() == "Hello, world"
    assert skin.get_variable("Rel") == "0.5"
    assert skin.meter("MeterText").text == "HEY!"
    skin.execute('[!CommandMeasure MeasureScript "SKIN:Bang(\'[!SetVariable Name lua]\')"]')
    assert skin.get_variable("Name") == "lua"


def test_input_text(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeasureInput]
        Measure=Plugin
        Plugin=InputText
        X=10
        W=200
        H=24
        DefaultValue=type here
        Command1=[!SetVariable Query "$UserInput$"] DefaultValue="first" X=5
        Command2=["https://example.com/?q=$UserInput$"]
    """)
    skin.execute('[!CommandMeasure MeasureInput "ExecuteBatch 1-2"]')
    rect, default, cb = host.prompts.pop(0)
    assert rect == (5, 0, 200, 24) and default == "first"
    cb("cats")
    assert skin.get_variable("Query") == "cats"
    rect, default, cb = host.prompts.pop(0)
    assert default == "type here"
    cb("dogs")
    assert host.commands == ["https://example.com/?q=dogs"]


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="needs a POSIX shell")
def test_run_command_output(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeasureRun]
        Measure=Plugin
        Plugin=RunCommand
        Parameter=echo hello $((20+1))
        FinishAction=["ran"]
    """)
    skin.execute('[!CommandMeasure MeasureRun "Run"]')
    host.drain(timeout=10)
    m = skin.measure("MeasureRun")
    assert m.string_value() == "hello 21"
    assert host.commands == ["ran"]


def test_quote_and_folder_info(tmp_path: Path) -> None:
    files = {"Test/quotes.txt": "one\n---\ntwo\n---\nthree\n", "Test/data/a.txt": "12345", "Test/data/sub/b.txt": "1"}
    skin, host = make_skin(tmp_path, """
        [MeasureQuote]
        Measure=Plugin
        Plugin=QuotePlugin
        PathName=quotes.txt
        Separator=---

        [MeasureSize]
        Measure=Plugin
        Plugin=FolderInfo
        Folder=data
        InfoType=FolderSize
        IncludeSubFolders=1

        [MeasureCount]
        Measure=Plugin
        Plugin=FolderInfo
        Folder=data
        InfoType=FileCount
    """, files=files)
    assert skin.measure("MeasureQuote").string_value() in ("one", "two", "three")
    for _ in range(100):
        host.drain(timeout=0.05)
        if skin.measure("MeasureSize").value and skin.measure("MeasureCount").value:
            break
    assert skin.measure("MeasureSize").value == 6
    assert skin.measure("MeasureCount").value == 1


def test_unknown_plugin_is_harmless(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureX]
        Measure=Plugin
        Plugin=SomeWindowsOnlyThing.dll
    """)
    skin.update()
    assert skin.measure("MeasureX").value == 0
