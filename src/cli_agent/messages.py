"""Provider-neutral conversation model.

The canonical representation mirrors the Anthropic Messages API content-block
shape because it is the richest of the supported providers; other adapters
translate to and from it. Everything here is plain data and JSON-serializable
so sessions can be persisted and resumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]


@dataclass
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"
    complete: bool = field(default=True, compare=False)
    """False when the arguments could not be parsed (e.g. cut off by max_tokens).
    Not persisted: a resumed incomplete call has already received its error result."""


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


Block = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    role: Role
    content: list[Block]

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


# Normalized stop reasons. Providers map their own values onto these.
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass
class Completion:
    message: Message
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    model: str = ""


def block_to_dict(block: Block) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def block_from_dict(data: dict[str, Any]) -> Block:
    kind = data.get("type")
    if kind == "text":
        return TextBlock(text=str(data["text"]))
    if kind == "tool_use":
        return ToolUseBlock(id=str(data["id"]), name=str(data["name"]), input=dict(data["input"]))
    if kind == "tool_result":
        return ToolResultBlock(
            tool_use_id=str(data["tool_use_id"]),
            content=str(data["content"]),
            is_error=bool(data.get("is_error", False)),
        )
    raise ValueError(f"unknown content block type: {kind!r}")


def message_to_dict(msg: Message) -> dict[str, Any]:
    return {"role": msg.role, "content": [block_to_dict(b) for b in msg.content]}


def message_from_dict(data: dict[str, Any]) -> Message:
    role = data.get("role")
    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role!r}")
    return Message(role=role, content=[block_from_dict(b) for b in data["content"]])
