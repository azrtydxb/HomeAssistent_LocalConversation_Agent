"""Tests for the conversation entity's stream translation."""

from typing import Any

import pytest
from homeassistant.components import conversation
from homeassistant.helpers import llm

from custom_components.local_llm_conversation.conversation import (
    _convert_content,
    _transform_stream,
)


async def choices(items: list[dict[str, Any]]):
    """Yield SSE choice dicts like the client does."""
    for item in items:
        yield item


async def collect(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [delta async for delta in _transform_stream(choices(items))]


def text_of(deltas: list[dict[str, Any]], key: str) -> str:
    return "".join(delta[key] for delta in deltas if key in delta)


async def test_content_deltas_are_forwarded_incrementally() -> None:
    """Early TTS depends on deltas arriving as they stream, not batched at the end."""
    deltas = await collect(
        [
            {"delta": {"content": "The light "}},
            {"delta": {"content": "is on."}},
        ]
    )
    assert deltas[0] == {"role": "assistant"}
    assert [d for d in deltas if "content" in d] == [
        {"content": "The light "},
        {"content": "is on."},
    ]


async def test_reasoning_content_field_routes_to_thinking() -> None:
    """Endpoints with a reasoning parser must not have reasoning spoken aloud."""
    deltas = await collect(
        [
            {"delta": {"reasoning_content": "the user wants light"}},
            {"delta": {"content": "Done."}},
        ]
    )
    assert text_of(deltas, "thinking_content") == "the user wants light"
    assert text_of(deltas, "content") == "Done."


async def test_inline_think_tags_route_to_thinking() -> None:
    """Models without a reasoning parser emit <think> inline in content."""
    deltas = await collect(
        [
            {"delta": {"content": "<thi"}},
            {"delta": {"content": "nk>reasoning</think>Done."}},
        ]
    )
    assert text_of(deltas, "thinking_content") == "reasoning"
    assert text_of(deltas, "content") == "Done."


async def test_tool_calls_emitted_once_arguments_are_complete() -> None:
    """Partial JSON must never reach the tool executor."""
    deltas = await collect(
        [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "HassTurnOn", "arguments": '{"name":'},
                        }
                    ]
                }
            },
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '"kitchen"}'}}
                    ]
                }
            },
        ]
    )
    tool_deltas = [d for d in deltas if "tool_calls" in d]
    assert len(tool_deltas) == 1
    (tool_call,) = tool_deltas[0]["tool_calls"]
    assert tool_call.id == "call_1"
    assert tool_call.tool_name == "HassTurnOn"
    assert tool_call.tool_args == {"name": "kitchen"}


async def test_tool_call_ids_survive_the_round_trip() -> None:
    """OpenAI requires tool results to reference the assistant's tool_call id."""
    assistant = conversation.AssistantContent(
        agent_id="conversation.test",
        tool_calls=[
            llm.ToolInput(
                id="call_9", tool_name="HassTurnOn", tool_args={"name": "lamp"}
            )
        ],
    )
    result = conversation.ToolResultContent(
        agent_id="conversation.test",
        tool_call_id="call_9",
        tool_name="HassTurnOn",
        tool_result={"speech": {}},
    )

    (assistant_message,) = _convert_content(assistant)
    (result_message,) = _convert_content(result)

    assert assistant_message["tool_calls"][0]["id"] == "call_9"
    assert result_message["tool_call_id"] == "call_9"
    assert result_message["role"] == "tool"


async def test_thinking_is_not_replayed_to_the_model() -> None:
    """Feeding a model its own reasoning back wastes context."""
    content = conversation.AssistantContent(
        agent_id="conversation.test",
        content="Done.",
        thinking_content="lots of reasoning",
    )
    (message,) = _convert_content(content)
    assert message == {"role": "assistant", "content": "Done."}


async def test_reasoning_key_variant_routes_to_thinking() -> None:
    """fastllm proxy names the field "reasoning", vLLM "reasoning_content"."""
    deltas = await collect(
        [
            {"delta": {"role": "assistant", "content": ""}},
            {"delta": {"reasoning": "Here's a thinking process:"}},
            {"delta": {"content": "Blue."}},
        ]
    )
    assert text_of(deltas, "thinking_content") == "Here's a thinking process:"
    assert text_of(deltas, "content") == "Blue."


async def test_token_limit_reached_while_reasoning_raises() -> None:
    """Budget spent entirely on reasoning must not surface as silence."""
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="token limit"):
        await collect(
            [
                {"delta": {"reasoning": "thinking and thinking"}},
                {"delta": {}, "finish_reason": "length"},
            ]
        )


async def test_token_limit_after_an_answer_is_not_an_error() -> None:
    """A truncated but non-empty answer is still worth speaking."""
    deltas = await collect(
        [
            {"delta": {"content": "The light is"}},
            {"delta": {}, "finish_reason": "length"},
        ]
    )
    assert text_of(deltas, "content") == "The light is"


async def test_real_endpoint_tool_call_shape() -> None:
    """Deltas captured verbatim from the fastllm proxy running Qwen3."""
    deltas = await collect(
        [
            {"delta": {"role": "assistant", "content": ""}},
            {"delta": {"reasoning": "The user wants the kitchen light on."}},
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "chatcmpl-tool-86d0a8fc6dafce15",
                            "type": "function",
                            "index": 0,
                            "function": {"name": "HassTurnOn"},
                        }
                    ]
                }
            },
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '{"name": "k'}}
                    ]
                }
            },
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": "itchen light"}}
                    ]
                }
            },
            {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"}'}}]}},
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
    )
    (tool_delta,) = [d for d in deltas if "tool_calls" in d]
    (tool_call,) = tool_delta["tool_calls"]
    assert tool_call.tool_name == "HassTurnOn"
    assert tool_call.tool_args == {"name": "kitchen light"}
    assert tool_call.id == "chatcmpl-tool-86d0a8fc6dafce15"


async def test_tools_are_formatted_with_the_serializer_home_assistant_supplies() -> (
    None
):
    """Guards the schema-conversion library, which changed between HA releases.

    2025.9 used voluptuous-openapi; 2026.x uses probatio, and llm.selector_serializer
    emits whichever format that release expects. Importing the wrong one broke setup
    at import time while every other test still passed.
    """
    import voluptuous as vol

    from custom_components.local_llm_conversation.conversation import _format_tool

    class FakeTool(llm.Tool):
        name = "HassTurnOn"
        description = "Turns on a device"
        parameters = vol.Schema({vol.Required("name"): str})

    formatted = _format_tool(FakeTool(), llm.selector_serializer)

    assert formatted["type"] == "function"
    assert formatted["function"]["name"] == "HassTurnOn"
    assert formatted["function"]["description"] == "Turns on a device"
    params = formatted["function"]["parameters"]
    assert params["type"] == "object"
    assert "name" in params["properties"]
    assert params["required"] == ["name"]


async def test_blank_lines_after_reasoning_are_not_spoken() -> None:
    """Models leave newlines between a think block and the answer."""
    deltas = await collect(
        [
            {"delta": {"content": "<think>a</think>"}},
            {"delta": {"content": "\n\nThe sky is blue."}},
        ]
    )
    assert text_of(deltas, "content") == "The sky is blue."


async def test_interior_newlines_are_preserved() -> None:
    """Only the leading gap is trimmed; list formatting must survive."""
    deltas = await collect(
        [
            {"delta": {"reasoning": "counting"}},
            {"delta": {"content": "\n\nTwo lights:\n- one\n- two"}},
        ]
    )
    assert text_of(deltas, "content") == "Two lights:\n- one\n- two"


async def test_whitespace_only_response_still_reports_token_exhaustion() -> None:
    """Trimmed-away whitespace must not count as having answered."""
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="token limit"):
        await collect(
            [
                {"delta": {"content": "\n\n"}},
                {"delta": {}, "finish_reason": "length"},
            ]
        )
