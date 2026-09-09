"""Tests for the stream-processing helpers."""

import pytest

from custom_components.local_llm_conversation.streaming import (
    ThinkSplitter,
    ToolCallAccumulator,
)


def split(chunks: list[str]) -> list[tuple[str, str]]:
    """Run chunks through a splitter and return every emitted part."""
    splitter = ThinkSplitter()
    parts: list[tuple[str, str]] = []
    for chunk in chunks:
        parts.extend(splitter.feed(chunk))
    parts.extend(splitter.flush())
    return parts


def joined(parts: list[tuple[str, str]], kind: str) -> str:
    return "".join(text for part_kind, text in parts if part_kind == kind)


def test_plain_content_passes_through_unchanged() -> None:
    parts = split(["The ", "kitchen ", "light is on."])
    assert joined(parts, "content") == "The kitchen light is on."
    assert joined(parts, "thinking") == ""


def test_thinking_block_is_separated_from_content() -> None:
    parts = split(["<think>user wants lights</think>Turning them on."])
    assert joined(parts, "content") == "Turning them on."
    assert joined(parts, "thinking") == "user wants lights"


def test_open_tag_split_across_chunks_is_not_spoken() -> None:
    """A tag straddling a chunk boundary must not leak into content."""
    parts = split(["<thi", "nk>reasoning</think>Done."])
    assert joined(parts, "content") == "Done."
    assert joined(parts, "thinking") == "reasoning"


def test_close_tag_split_across_chunks() -> None:
    parts = split(["<think>reasoning</thi", "nk>Done."])
    assert joined(parts, "content") == "Done."
    assert joined(parts, "thinking") == "reasoning"


def test_tag_split_one_character_at_a_time() -> None:
    """The worst case: every character arrives in its own chunk."""
    stream = "<think>why</think>Hello"
    parts = split(list(stream))
    assert joined(parts, "content") == "Hello"
    assert joined(parts, "thinking") == "why"


def test_lone_angle_bracket_is_not_withheld_forever() -> None:
    """Text that merely looks like a tag prefix must still be released."""
    parts = split(["5 < 7 and 8 > 3"])
    assert joined(parts, "content") == "5 < 7 and 8 > 3"


def test_partial_tag_at_end_of_stream_is_flushed_as_content() -> None:
    """An unterminated prefix is real text once the stream ends."""
    parts = split(["Result: <thi"])
    assert joined(parts, "content") == "Result: <thi"


def test_unclosed_think_block_stays_thinking() -> None:
    """A truncated response must not spill reasoning into speech."""
    parts = split(["<think>still reasoning when the stream died"])
    assert joined(parts, "content") == ""
    assert joined(parts, "thinking") == "still reasoning when the stream died"


def test_multiple_think_blocks() -> None:
    parts = split(["<think>a</think>One.<think>b</think>Two."])
    assert joined(parts, "content") == "One.Two."
    assert joined(parts, "thinking") == "ab"


def test_content_before_think_block_is_preserved() -> None:
    parts = split(["Hmm. <think>a</think>Yes."])
    assert joined(parts, "content") == "Hmm. Yes."


# --- tool call reassembly ---------------------------------------------------


def accumulate(deltas: list[list[dict]]) -> list[tuple[str, str, str]]:
    accumulator = ToolCallAccumulator()
    for delta in deltas:
        accumulator.feed(delta)
    return accumulator.finish()


def test_arguments_fragmented_across_deltas_are_rejoined() -> None:
    calls = accumulate(
        [
            [
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "turn_on", "arguments": '{"ent'},
                }
            ],
            [{"index": 0, "function": {"arguments": 'ity":"light.k'}}],
            [{"index": 0, "function": {"arguments": 'itchen"}'}}],
        ]
    )
    assert calls == [("call_1", "turn_on", '{"entity":"light.kitchen"}')]


def test_parallel_tool_calls_do_not_bleed_into_each_other() -> None:
    """Interleaved indices are the classic source of merged-argument bugs."""
    calls = accumulate(
        [
            [
                {
                    "index": 0,
                    "id": "a",
                    "function": {"name": "turn_on", "arguments": '{"e":"1"'},
                },
                {
                    "index": 1,
                    "id": "b",
                    "function": {"name": "turn_off", "arguments": '{"e":"2"'},
                },
            ],
            [
                {"index": 1, "function": {"arguments": "}"}},
                {"index": 0, "function": {"arguments": "}"}},
            ],
        ]
    )
    assert calls == [
        ("a", "turn_on", '{"e":"1"}'),
        ("b", "turn_off", '{"e":"2"}'),
    ]


def test_missing_index_falls_back_to_arrival_order() -> None:
    """Some OpenAI-compatible servers omit index entirely."""
    calls = accumulate([[{"id": "a", "function": {"name": "one", "arguments": "{}"}}]])
    assert calls == [("a", "one", "{}")]


def test_repeated_whole_name_is_not_duplicated() -> None:
    """Some servers resend the full function name on every delta."""
    calls = accumulate(
        [
            [
                {
                    "index": 0,
                    "id": "a",
                    "function": {"name": "get_state", "arguments": "{"},
                }
            ],
            [{"index": 0, "function": {"name": "get_state", "arguments": "}"}}],
        ]
    )
    assert calls == [("a", "get_state", "{}")]


def test_fragmented_name_is_rejoined() -> None:
    calls = accumulate(
        [
            [{"index": 0, "id": "a", "function": {"name": "get_"}}],
            [{"index": 0, "function": {"name": "state", "arguments": "{}"}}],
        ]
    )
    assert calls == [("a", "get_state", "{}")]


def test_empty_arguments_become_an_empty_object() -> None:
    """A no-argument tool may send no argument fragments at all."""
    calls = accumulate([[{"index": 0, "id": "a", "function": {"name": "list_areas"}}]])
    assert calls == [("a", "list_areas", "{}")]


def test_no_tool_calls_yields_nothing() -> None:
    assert accumulate([]) == []


# --- prompted tool calling, for models with no native support ---------------


def test_a_bare_json_object_is_read_as_a_tool_call() -> None:
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    call = parse_prompted_tool_call(
        '{"tool": "HassTurnOn", "arguments": {"name": "lamp"}}'
    )
    assert call == ("HassTurnOn", '{"name": "lamp"}', {"name": "lamp"})


def test_a_fenced_block_is_read_too() -> None:
    """Small models fence JSON even when told not to."""
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    text = '```json\n{"tool": "HassTurnOff", "arguments": {"name": "lamp"}}\n```'
    name, _, args = parse_prompted_tool_call(text)
    assert (name, args) == ("HassTurnOff", {"name": "lamp"})


def test_a_tool_with_no_arguments_is_allowed() -> None:
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    assert parse_prompted_tool_call('{"tool": "GetLiveContext"}') == (
        "GetLiveContext",
        "{}",
        {},
    )


def test_an_ordinary_answer_is_not_mistaken_for_a_call() -> None:
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    assert parse_prompted_tool_call("The kitchen light is on.") is None


def test_a_reply_merely_mentioning_a_tool_is_not_a_call() -> None:
    """A model explaining itself must not be executed."""
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    text = 'I could call {"tool": "HassTurnOn"} but I will not.'
    assert parse_prompted_tool_call(text) is None


def test_malformed_json_is_not_a_call() -> None:
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    assert parse_prompted_tool_call('{"tool": "HassTurnOn", "arguments":') is None


def test_a_nameless_or_odd_shape_is_rejected() -> None:
    from custom_components.local_llm_conversation.streaming import (
        parse_prompted_tool_call,
    )

    assert parse_prompted_tool_call('{"arguments": {"name": "lamp"}}') is None
    assert parse_prompted_tool_call('{"tool": ""}') is None
    assert parse_prompted_tool_call('{"tool": "X", "arguments": "lamp"}') is None
    assert parse_prompted_tool_call("[1, 2, 3]") is None


def test_the_tool_list_reaches_the_prompt() -> None:
    from custom_components.local_llm_conversation.streaming import (
        prompted_tool_prompt,
    )

    prompt = prompted_tool_prompt(
        [
            {
                "type": "function",
                "function": {
                    "name": "HassTurnOn",
                    "description": "Turns on a device",
                    "parameters": {"type": "object"},
                },
            }
        ]
    )
    assert "HassTurnOn" in prompt
    assert "Turns on a device" in prompt
    # The braces in the instructions must survive formatting.
    assert '{"tool": "<tool name>"' in prompt
