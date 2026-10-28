"""Scripted provider for tests and offline demos.

A script is a list of turns; each turn is either a Completion-like dict
(``{"text": ..., "tool_calls": [{"name": ..., "input": {...}}]}``) or an
exception instance to raise. Every request the agent makes is recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli_agent.messages import Completion, Message, StopReason, TextBlock, ToolUseBlock, Usage
from cli_agent.providers.base import ProviderError, ToolSpec


@dataclass
class RecordedRequest:
    system: str
    messages: list[Message]
    tools: list[ToolSpec]
    max_tokens: int


@dataclass
class FakeProvider:
    script: list[Any]
    model: str = "fake-model"
    name: str = "fake"
    requests: list[RecordedRequest] = field(default_factory=list)
    _counter: int = 0

    @classmethod
    def from_file(cls, path: Path) -> FakeProvider:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("fake script must be a JSON list of turns")
        return cls(script=data)

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Completion:
        self.requests.append(RecordedRequest(system, list(messages), list(tools), max_tokens))
        if not self.script:
            raise ProviderError("fake provider script exhausted")
        turn = self.script.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        blocks: list[TextBlock | ToolUseBlock] = []
        if turn.get("text"):
            blocks.append(TextBlock(text=turn["text"]))
        for call in turn.get("tool_calls", []):
            self._counter += 1
            blocks.append(
                ToolUseBlock(
                    id=call.get("id", f"toolu_fake_{self._counter}"),
                    name=call["name"],
                    input=call.get("input", {}),
                )
            )
        stop: StopReason = turn.get("stop_reason") or (
            "tool_use" if any(isinstance(b, ToolUseBlock) for b in blocks) else "end_turn"
        )
        return Completion(
            message=Message(role="assistant", content=list(blocks)),
            stop_reason=stop,
            usage=Usage(input_tokens=10, output_tokens=5),
            model=self.model,
        )
