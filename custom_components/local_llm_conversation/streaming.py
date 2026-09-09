"""Pure stream-processing helpers.

Deliberately free of Home Assistant imports so the fiddly parts can be tested
without a running Home Assistant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

PartKind = Literal["content", "thinking"]


def _held_prefix_len(buf: str, tag: str) -> int:
    """Return how many trailing chars of buf could still grow into tag.

    A chunk boundary can fall inside a tag ("<thi" + "nk>"), so any suffix of
    the buffer that is a proper prefix of the tag must be withheld until the
    next chunk disambiguates it.
    """
    for size in range(min(len(tag) - 1, len(buf)), 0, -1):
        if buf.endswith(tag[:size]):
            return size
    return 0


@dataclass
class ThinkSplitter:
    """Split a token stream into spoken content and thinking content.

    Models served with a reasoning parser return thinking in a separate
    ``reasoning_content`` field, which needs no splitting. Models without one
    emit ``<think>...</think>`` inline in ``content``, and the tags can straddle
    chunk boundaries.
    """

    _buffer: str = ""
    _thinking: bool = False

    def feed(self, chunk: str) -> list[tuple[PartKind, str]]:
        """Consume a chunk, returning the parts that are now unambiguous."""
        self._buffer += chunk
        parts: list[tuple[PartKind, str]] = []

        while True:
            tag = THINK_CLOSE if self._thinking else THINK_OPEN
            index = self._buffer.find(tag)
            if index != -1:
                if index:
                    parts.append((self._kind, self._buffer[:index]))
                self._buffer = self._buffer[index + len(tag) :]
                self._thinking = not self._thinking
                continue

            held = _held_prefix_len(self._buffer, tag)
            emit = self._buffer[: len(self._buffer) - held] if held else self._buffer
            self._buffer = self._buffer[len(self._buffer) - held :] if held else ""
            if emit:
                parts.append((self._kind, emit))
            return parts

    def flush(self) -> list[tuple[PartKind, str]]:
        """Release anything still held once the stream has ended."""
        if not self._buffer:
            return []
        parts = [(self._kind, self._buffer)]
        self._buffer = ""
        return parts

    @property
    def _kind(self) -> PartKind:
        return "thinking" if self._thinking else "content"


class ToolCallParseError(ValueError):
    """Raised when a model emits tool call arguments that are not valid JSON."""


@dataclass
class _PartialToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class ToolCallAccumulator:
    """Reassemble tool calls that arrive split across streaming deltas.

    Backends disagree on the details: ``id`` and ``name`` usually appear only in
    the first delta for a call, ``arguments`` arrives as a JSON string in
    fragments, and some servers omit ``index`` entirely. Arguments are only
    valid JSON once every fragment has arrived, so calls are emitted at the end
    of the stream rather than as they appear.
    """

    _calls: dict[int, _PartialToolCall] = field(default_factory=dict)

    def feed(self, deltas: list[dict[str, Any]]) -> None:
        """Merge a delta's ``tool_calls`` list into the accumulator."""
        for delta in deltas:
            # Servers that omit index send calls sequentially; append instead.
            index = delta.get("index")
            if index is None:
                index = len(self._calls)
            call = self._calls.setdefault(index, _PartialToolCall())

            if call_id := delta.get("id"):
                call.id = call_id
            function = delta.get("function") or {}
            if name := function.get("name"):
                # Fragmented names concatenate; whole names repeat identically.
                call.name = name if name.startswith(call.name) else call.name + name
            if (arguments := function.get("arguments")) is not None:
                call.arguments += arguments

    def finish(self) -> list[tuple[str, str, str]]:
        """Return completed calls as ``(id, name, arguments_json)`` tuples."""
        return [
            (call.id, call.name, call.arguments or "{}")
            for _, call in sorted(self._calls.items())
            if call.name
        ]


# What a model without native tool calling is asked to emit. Kept minimal: small
# models follow one short rule far better than a long specification.
PROMPTED_TOOL_INSTRUCTIONS = """
To use a tool, reply with nothing but a JSON object of this exact shape:

{{"tool": "<tool name>", "arguments": {{<arguments>}}}}

Do not wrap it in prose. If no tool is needed, answer normally and emit no JSON.

Available tools:
{tools}
"""


def prompted_tool_prompt(tools: list[dict[str, Any]]) -> str:
    """Describe the tools in the prompt, for models that take none natively."""
    described = "\n".join(
        json.dumps(
            {
                "tool": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "arguments": tool["function"].get("parameters", {}),
            }
        )
        for tool in tools
    )
    return PROMPTED_TOOL_INSTRUCTIONS.format(tools=described)


def parse_prompted_tool_call(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Find a tool call in a model's plain text reply.

    Returns ``(name, arguments_json, arguments)`` or None when the reply is an
    ordinary answer. Only a reply that is *only* a tool call counts: a model
    describing a tool mid-sentence is answering, not calling.
    """
    stripped = text.strip()
    # Models fence JSON even when told not to.
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0].strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    name = parsed.get("tool")
    if not isinstance(name, str) or not name:
        return None
    arguments = parsed.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None

    return name, json.dumps(arguments), arguments
