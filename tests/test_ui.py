import io
import json

from cli_agent.messages import ToolUseBlock
from cli_agent.ui import JsonUI, TerminalUI, escape_controls


def test_escape_controls() -> None:
    assert escape_controls("a\x1b[2Kb\rc") == "a\\x1b[2Kb\\rc"
    assert escape_controls("x\ny\tz") == "x\ny\tz"
    assert escape_controls("x\ny", keep_newlines=False) == "x\\ny"


def make_ui(answers: str) -> tuple[TerminalUI, io.StringIO]:
    err = io.StringIO()
    return TerminalUI(interactive=True, out=io.StringIO(), err=err, inp=io.StringIO(answers)), err


def test_approval_prompt_shows_escaped_command() -> None:
    ui, err = make_ui("y\n")
    call = ToolUseBlock("t", "run_shell", {"command": "ls\rrm -rf x"})
    assert ui.approve(call, "$ ls\rrm -rf x") == "yes"
    assert "$ ls\\rrm -rf x" in err.getvalue() and "\r" not in err.getvalue()


def test_approval_answers() -> None:
    call = ToolUseBlock("t", "run_shell", {"command": "ls"})
    for text, expected in [
        ("a\n", "always"),
        ("n\n", "no"),
        ("\n", "no"),
        ("", "no"),
        ("what\ny\n", "yes"),
    ]:
        ui, _ = make_ui(text)
        assert ui.approve(call, "$ ls") == expected


def test_view_full_content() -> None:
    content = "\n".join(f"line{i}" for i in range(50))
    ui, err = make_ui("v\nn\n")
    call = ToolUseBlock("t", "write_file", {"path": "f", "content": content})
    assert ui.approve(call, "write f") == "no"
    out = err.getvalue()
    assert "30 more lines" in out and "line49" in out


def test_non_interactive_never_reads_input() -> None:
    inp = io.StringIO("y\n")
    ui = TerminalUI(interactive=False, out=io.StringIO(), err=io.StringIO(), inp=inp)
    assert ui.approve(ToolUseBlock("t", "run_shell", {}), "$ x") == "no"
    assert inp.tell() == 0


def test_json_ui_events() -> None:
    out = io.StringIO()
    ui = JsonUI(out=out)
    ui.on_text("hi")
    assert ui.approve(ToolUseBlock("t", "run_shell", {}), "$ x") == "no"
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [e["event"] for e in events] == ["text", "approval_denied"]
