"""Regressions for the fourth independent review (see docs/progress.md)."""

import itertools
from pathlib import Path

import pytest

from cli_agent.agent import CONTINUE_PROMPT
from cli_agent.messages import Message, TextBlock
from cli_agent.providers.base import ProviderError, with_deadline
from cli_agent.tools import ToolContext, ToolError
from cli_agent.tools.files import EditFile, ReadFile, fuzzy_replace
from tests.test_agent import make_agent, new_session


def test_fuzzy_keeps_form_feeds_and_other_separators() -> None:
    text = "    a = 1\x0c    b = 2\n"
    out = fuzzy_replace(text, "a = 1", "a = 10")
    assert out is None or out[0].count("\x0c") == 1


def test_fuzzy_cr_only_file_not_mangled() -> None:
    text = "    a\r    b\r    c\r"
    out = fuzzy_replace(text, "a\nb", "a\nB")
    assert out is None or "\n" not in out[0]


def test_fuzzy_noop_edit_is_reported(tmp_path: Path) -> None:
    ctx = ToolContext(root=tmp_path)
    (tmp_path / "m.py").write_text("    x = 1\n")
    ReadFile().run({"path": "m.py"}, ctx)
    with pytest.raises(ToolError, match="not change"):
        EditFile().run({"path": "m.py", "old_string": "x = 1 ", "new_string": "x = 1"}, ctx)


def test_fuzzy_failed_match_is_fast() -> None:
    import time

    text = "".join(f"line {i}\n" for i in range(50_000))
    old = "".join(f"other {i}\n" for i in range(1500))
    start = time.monotonic()
    assert fuzzy_replace(text, old, "x") is None
    assert time.monotonic() - start < 2


def test_deadline_resets_on_content_but_not_pings() -> None:
    import time

    def slow_content():  # type: ignore[no-untyped-def]
        for i in range(5):
            time.sleep(0.03)
            yield f'data: {{"type": "content_block_delta", "i": {i}}}'

    assert len(list(with_deadline(slow_content(), seconds=0.1))) == 5
    pings = itertools.repeat('data: {"type": "ping"}')
    with pytest.raises(ProviderError, match="no content"):
        for _ in with_deadline(pings, seconds=0.05):
            pass


def test_stale_continue_prompt_dropped_before_next_prompt(tmp_path: Path) -> None:
    script = [
        {"text": "part 1", "stop_reason": "max_tokens"},
        ProviderError("HTTP 401: bad key"),
        {"text": "the time is noon"},
    ]
    agent, provider = make_agent(tmp_path, script)
    session = new_session(tmp_path)
    assert agent.run(session, "explain A").status == "error"
    agent.run(session, "forget A; just tell me the time")
    last_user = provider.requests[-1].messages[-1]
    texts = [b.text for b in last_user.content if isinstance(b, TextBlock)]
    assert texts == ["forget A; just tell me the time"]
    assert all(CONTINUE_PROMPT not in m.text() for m in session.messages if isinstance(m, Message))


def test_final_text_joins_continued_replies(tmp_path: Path) -> None:
    script = [
        {"text": "Answer part 1...", "stop_reason": "max_tokens"},
        {"text": "...and the fix is X."},
    ]
    agent, _ = make_agent(tmp_path, script)
    result = agent.run(new_session(tmp_path), "go")
    assert result.final_text == "Answer part 1......and the fix is X."
